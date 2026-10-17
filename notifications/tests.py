from datetime import time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from classroom.models import Assignment, Submission
from courses.models import Course, Enrollment
from delivery.models import AttendanceRecord, ClassSession
from exams.models import AssessmentComponent, Exam, ExamQuestion, Question
from institution.tests import make_institution

from .models import Deadline, DeadlineAck, Notification
from .services import deadline_report, refresh


class NotificationTests(TestCase):
    def setUp(self):
        self.inst, self.dept, self.prog = make_institution()
        self.teacher = User.objects.create_user("t1", password="pw123456", role=User.TEACHER, first_name="Tea")
        self.student = User.objects.create_user("s1", password="pw123456", role=User.STUDENT, first_name="Stu")
        self.admin = User.objects.create_user("adm", password="pw123456", role=User.TEACHER, is_staff=True)
        self.course = Course.objects.create(teacher=self.teacher, code="CS101", title="Programming", program=self.prog, year=1)
        Enrollment.objects.create(course=self.course, student=self.student)
        self.today = timezone.localdate()

    def open_keys(self, user):
        return set(Notification.objects.filter(user=user, resolved_at__isnull=True).values_list("key", flat=True))

    def test_attendance_item_appears_and_resolves_itself(self):
        s = ClassSession.objects.create(course=self.course, date=self.today - timedelta(days=5), start_time=time(10), end_time=time(11))
        refresh(self.teacher)
        n = Notification.objects.get(user=self.teacher, key=f"att:{self.course.pk}")
        self.assertEqual(n.level, Notification.URGENT)  # older than the grace period
        AttendanceRecord.objects.create(session=s, student=self.student)
        s.status = ClassSession.COMPLETED
        s.save()
        refresh(self.teacher)
        n.refresh_from_db()
        self.assertIsNotNone(n.resolved_at)

    def test_grading_and_student_assignment_items(self):
        a = Assignment.objects.create(course=self.course, title="Lab 1", max_points=Decimal("10"), status=Assignment.PUBLISHED,
                                      due_at=timezone.now() + timedelta(days=1))
        refresh(self.student)
        self.assertIn(f"hw:{a.pk}", self.open_keys(self.student))
        Submission.objects.create(assignment=a, student=self.student, status=Submission.TURNED_IN, submitted_at=timezone.now())
        refresh(self.student)
        refresh(self.teacher)
        self.assertNotIn(f"hw:{a.pk}", self.open_keys(self.student))
        self.assertIn(f"grade:{a.pk}", self.open_keys(self.teacher))

    def test_paper_deadline_checked_automatically(self):
        d = Deadline.objects.create(title="Submit MSE papers", kind=Deadline.PAPER, component_kind="MSE",
                                    due_at=timezone.now() + timedelta(days=2), remind_days=7)
        refresh(self.teacher)
        n = Notification.objects.get(user=self.teacher, key=f"dl:{d.pk}:{self.course.pk}")
        self.assertEqual(n.level, Notification.WARN)
        self.assertEqual(deadline_report(d)["done"], 0)
        refresh(self.admin)
        self.assertIn(f"adm-dl:{d.pk}", self.open_keys(self.admin))
        comp = AssessmentComponent.objects.create(course=self.course, name="MSE", kind="MSE", weight=Decimal("20"))
        exam = Exam.objects.create(course=self.course, component=comp, title="MSE", status=Exam.PUBLISHED,
                                   date=self.today + timedelta(days=20))
        q = Question.objects.create(course=self.course, text="Explain recursion")
        ExamQuestion.objects.create(exam=exam, question=q, label="Q1", marks=Decimal("5"))
        refresh(self.teacher)
        refresh(self.admin)
        self.assertNotIn(f"dl:{d.pk}:{self.course.pk}", self.open_keys(self.teacher))
        self.assertNotIn(f"adm-dl:{d.pk}", self.open_keys(self.admin))
        self.assertEqual(deadline_report(d)["done"], 1)

    def test_overdue_deadline_escalates_and_custom_deadline_ack(self):
        d = Deadline.objects.create(title="Course file", kind=Deadline.CUSTOM, audience=Deadline.ALL_STAFF,
                                    due_at=timezone.now() + timedelta(days=20), remind_days=3)
        refresh(self.teacher)
        n = Notification.objects.get(user=self.teacher, key=f"dl:{d.pk}")
        self.assertEqual(n.level, Notification.INFO)
        n.read_at = timezone.now()
        n.save()
        d.due_at = timezone.now() - timedelta(hours=1)
        d.save()
        refresh(self.teacher)
        n.refresh_from_db()
        self.assertEqual(n.level, Notification.URGENT)
        self.assertIsNone(n.read_at)  # escalation shows it again
        self.client.login(username="t1", password="pw123456")
        self.client.post(reverse("notification_center"), {"action": "ack", "deadline": d.pk})
        self.assertTrue(DeadlineAck.objects.filter(deadline=d, user=self.teacher).exists())
        refresh(self.teacher)
        self.assertNotIn(f"dl:{d.pk}", self.open_keys(self.teacher))
        self.assertNotIn(f"dl:{d.pk}", self.open_keys(self.student))  # staff only

    def test_pages_and_access(self):
        Deadline.objects.create(title="Notice", kind=Deadline.NOTICE, audience=Deadline.EVERYONE)
        for user in ("t1", "s1", "adm"):
            self.client.login(username=user, password="pw123456")
            r = self.client.get(reverse("notification_center"))
            self.assertContains(r, "Notice")
            self.assertEqual(self.client.get(reverse("notification_count")).status_code, 200)
        self.client.login(username="s1", password="pw123456")
        self.assertIn(self.client.get(reverse("deadlines")).status_code, (302, 403))
        other = Notification.objects.filter(user=self.teacher).first() or Notification.objects.create(user=self.teacher, key="x", title="x")
        self.assertEqual(self.client.get(reverse("notification_go", args=[other.pk])).status_code, 404)
        self.client.login(username="adm", password="pw123456")
        r = self.client.post(reverse("deadlines"), {"title": "Release ESE marks", "kind": "marks", "component_kind": "ESE",
                                                    "due_at": (timezone.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M"),
                                                    "remind_days": 5, "audience": "teachers", "active": "on"})
        d = Deadline.objects.get(title="Release ESE marks")
        self.assertRedirects(r, reverse("deadline_detail", args=[d.pk]))
        self.assertContains(self.client.get(reverse("deadline_detail", args=[d.pk])), "CS101")
