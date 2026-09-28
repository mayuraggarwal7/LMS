from datetime import date, time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from classroom import grading
from classroom.models import Assignment
from courses.models import Course, Enrollment, Topic, Unit
from courses.planner import generate_learning_plan
from delivery.models import ClassSession, TimetableSlot
from delivery.services import add_supplementary_day, capacity, declare_holiday, reschedule_session, sync_timetable
from exams import services as exam_services
from exams.analytics import distribute_exam_total
from exams.models import Exam


def make_course(teacher, code="C1", start=date(2026, 7, 6), end=date(2026, 7, 31), hours=8):
    course = Course.objects.create(teacher=teacher, code=code, title="Course", start_date=start, end_date=end)
    unit = Unit.objects.create(course=course, number=1, title="Basics", hours=hours)
    for i in range(hours):
        Topic.objects.create(unit=unit, title=f"Topic {i}", order=i)
    generate_learning_plan(course)
    TimetableSlot.objects.create(course=course, weekday=0, start_time=time(10), end_time=time(11))  # Mondays
    TimetableSlot.objects.create(course=course, weekday=2, start_time=time(10), end_time=time(11))  # Wednesdays
    sync_timetable(course)
    return course


class ScheduleChangeTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user("t", password="pw123456", role=User.TEACHER)
        self.course = make_course(self.teacher)
        self.other = make_course(self.teacher, code="C2")

    def lectures(self, course):
        return list(course.sessions.filter(status=ClassSession.SCHEDULED).order_by("date", "start_time"))

    def test_sudden_holiday_cancels_and_shifts_plan(self):
        before = self.lectures(self.course)
        day = before[1].date
        planned_that_day = before[1].plan_item
        declare_holiday([self.course, self.other], day, "Heavy rain")
        cancelled = self.course.sessions.get(date=day)
        self.assertEqual(cancelled.status, ClassSession.CANCELLED)
        self.assertEqual(cancelled.cancel_reason, "Holiday: Heavy rain")
        after = self.lectures(self.course)
        self.assertEqual(after[1].plan_item, planned_that_day)  # topic moved to the next class
        self.assertTrue(self.other.sessions.filter(date=day, status=ClassSession.CANCELLED).exists())
        self.assertEqual(capacity(self.course)["shortfall"], 1)  # 8 classes, 8 items -> one lost

    def test_supplementary_day_recovers_capacity(self):
        declare_holiday([self.course], self.lectures(self.course)[0].date, "Event")
        saturday = date(2026, 7, 11)
        add_supplementary_day([self.course], saturday, follows_weekday=0, reason="Compensation")
        extra = self.course.sessions.get(date=saturday)
        self.assertEqual(extra.status, ClassSession.SCHEDULED)
        self.assertEqual(extra.plan_item.sequence, 2)  # plan pulled forward onto the Saturday
        self.assertEqual(capacity(self.course)["shortfall"], 0)
        # supplementary days after the teaching end date also count
        add_supplementary_day([self.course], date(2026, 8, 1), follows_weekday=2)
        self.assertTrue(self.course.sessions.filter(date=date(2026, 8, 1)).exists())

    def test_reschedule_keeps_topic_and_record(self):
        session = self.lectures(self.course)[2]
        item = session.plan_item
        new = reschedule_session(session, date(2026, 7, 18), time(14), time(15))
        session.refresh_from_db()
        self.assertEqual(session.status, ClassSession.CANCELLED)
        self.assertIn("Rescheduled to 18 Jul 2026", session.cancel_reason)
        self.assertEqual(new.plan_item, item)
        sync_timetable(self.course)  # re-sync keeps the moved class and its topic
        new.refresh_from_db()
        self.assertEqual(new.plan_item, item)
        self.assertEqual(ClassSession.objects.filter(plan_item=item, status="scheduled").count(), 1)

    def test_schedule_changes_view_applies_to_all_classes(self):
        self.client.login(username="t", password="pw123456")
        day = date(2026, 7, 13)
        self.client.post(reverse("schedule_changes"), {"kind": "holiday", "date": day, "name": "Bandh",
                                                       "courses": [self.course.pk, self.other.pk]})
        self.assertEqual(ClassSession.objects.filter(date=day, status="cancelled").count(), 2)
        self.client.post(reverse("schedule_changes"), {"kind": "supplementary", "date": date(2026, 7, 25),
                                                       "follows_weekday": 0, "courses": [self.course.pk]})
        self.assertTrue(self.course.sessions.filter(date=date(2026, 7, 25)).exists())
        self.assertFalse(self.other.sessions.filter(date=date(2026, 7, 25)).exists())


class RetakeTests(TestCase):
    def setUp(self):
        from exams.services import create_default_scheme, create_exam, generate_question_bank

        self.teacher = User.objects.create_user("t", password="pw123456", role=User.TEACHER)
        self.course = make_course(self.teacher)
        create_default_scheme(self.course)
        generate_question_bank(self.course, use_ai=False)
        self.ese_comp = self.course.components.get(kind="ESE")
        self.ese, _ = create_exam(self.course, self.ese_comp, "ESE", 20, self.course.units.all(), 60)
        self.passed = User.objects.create_user("p", password="pw123456", roll_no="1")
        self.failed = User.objects.create_user("f", password="pw123456", roll_no="2")
        self.absent = User.objects.create_user("a", password="pw123456", roll_no="3")
        for s in (self.passed, self.failed, self.absent):
            Enrollment.objects.create(course=self.course, student=s)
        distribute_exam_total(self.ese, self.passed, Decimal("15"))
        distribute_exam_total(self.ese, self.failed, Decimal("5"))
        from exams.models import ExamAbsence
        ExamAbsence.objects.create(exam=self.ese, student=self.absent)

    def ese_percent(self, student):
        row = grading.build_gradebook(self.course, [student])["rows"][0]
        return next(c for c in row["components"] if c["component"].kind == "ESE")

    def test_reexam_candidates_and_replace_policy(self):
        candidates = exam_services.retake_candidates(self.ese, Exam.REEXAM)
        self.assertEqual(set(candidates), {self.failed, self.absent})
        reexam, _ = exam_services.create_retake(self.ese, Exam.REEXAM, Exam.REPLACE, candidates)
        self.assertEqual(reexam.total_marks, Decimal("20"))
        self.assertFalse(set(reexam.paper.values_list("question", flat=True)) & set(self.ese.paper.values_list("question", flat=True)))
        distribute_exam_total(reexam, self.failed, Decimal("12"))
        distribute_exam_total(reexam, self.absent, Decimal("10"))
        comp = self.ese_percent(self.failed)
        self.assertAlmostEqual(comp["percent"], 60.0)
        self.assertEqual(comp["cells"][0]["retake"], "RE")
        self.assertEqual(self.ese_percent(self.absent)["percent"], 50.0)
        self.assertAlmostEqual(self.ese_percent(self.passed)["percent"], 75.0)  # unaffected
        # students only see a released re-exam
        self.assertIsNone(grading.build_gradebook(self.course, [self.failed], released_only=True)["rows"][0]["final_percent"])

    def test_additional_assessment_best_policy_for_assignment(self):
        a = Assignment.objects.create(course=self.course, title="A1", max_points=Decimal("10"), status=Assignment.PUBLISHED,
                                      component=self.course.components.get(name="ISE-1"))
        grading.set_total_score(grading.get_or_create_submission(a, self.passed), Decimal("9"))
        grading.set_total_score(grading.get_or_create_submission(a, self.failed), Decimal("3"))
        candidates = exam_services.retake_candidates(a, Exam.ADDITIONAL)
        self.assertEqual(set(candidates), {self.failed, self.absent})
        extra, _ = exam_services.create_retake(a, Exam.ADDITIONAL, Exam.BEST, [self.failed, self.passed], total_marks=20)
        distribute_exam_total(extra, self.failed, Decimal("14"))  # 70% > 30% -> used
        distribute_exam_total(extra, self.passed, Decimal("10"))  # 50% < 90% -> original kept
        book = grading.build_gradebook(self.course)
        by_student = {r["student"]: r for r in book["rows"]}
        ise = lambda s: next(c for c in by_student[s]["components"] if c["component"].name == "ISE-1")  # noqa: E731
        self.assertEqual(ise(self.failed)["cells"][0]["score"], Decimal("7.00"))
        self.assertEqual(ise(self.failed)["cells"][0]["retake"], "ADD")
        self.assertEqual(ise(self.passed)["cells"][0]["score"], Decimal("9"))

    def test_retake_views(self):
        self.client.login(username="t", password="pw123456")
        url = reverse("retake_new", args=[self.course.pk]) + f"?exam={self.ese.pk}&purpose=reexam"
        r = self.client.get(url)
        self.assertEqual(set(r.context["suggested"]), {self.failed, self.absent})
        r = self.client.post(url, {"exam": self.ese.pk, "purpose": "reexam", "policy": "replace", "title": "Re-exam ESE",
                                   "duration_minutes": 60, "total_marks": 20, "candidates": [self.failed.pk]})
        reexam = Exam.objects.get(purpose="reexam")
        self.assertRedirects(r, reverse("exam_detail", args=[self.course.pk, reexam.pk]))
        r = self.client.get(reverse("exam_marks", args=[self.course.pk, reexam.pk]))
        self.assertEqual([row["student"] for row in r.context["rows"]], [self.failed])
        # non-candidates don't see the re-exam
        reexam.status = "published"
        reexam.save()
        self.client.login(username="p", password="pw123456")
        r = self.client.get(reverse("exam_list", args=[self.course.pk]))
        self.assertNotIn(reexam, [row["exam"] for row in r.context["rows"]])
