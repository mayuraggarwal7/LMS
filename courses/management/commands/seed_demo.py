"""Create a demo teacher, students and a fully configured course from the sample syllabus.

    python manage.py seed_demo

Logins: teacher / teacher123, students s01..s12 / student123
"""

import random
from datetime import time, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User
from classroom import grading
from classroom.models import Assignment, Submission
from courses import syllabus
from courses.models import AcademicTerm, Enrollment
from courses.setup import build_course
from delivery.models import AttendanceRecord, ClassSession, Holiday, TimetableSlot
from delivery.services import sync_timetable
from exams.models import ExamMark

NAMES = ["Aarav Shah", "Diya Kulkarni", "Ishaan Rao", "Ananya Iyer", "Vihaan Patil", "Meera Nair", "Kabir Singh",
         "Saanvi Joshi", "Arjun Menon", "Riya Deshpande", "Aditya Verma", "Tara Kapoor"]


class Command(BaseCommand):
    help = "Seed a demo teacher, 12 students and a configured Data Structures course"

    def handle(self, *args, **options):
        rng = random.Random(7)
        teacher, created = User.objects.get_or_create(
            username="teacher", defaults={"first_name": "Priya", "last_name": "Deshmukh", "role": User.TEACHER,
                                          "department": "Computer Engineering", "email": "teacher@example.edu"})
        if created:
            teacher.set_password("teacher123")
            teacher.save()
        students = []
        for i, name in enumerate(NAMES, start=1):
            first, last = name.split()
            s, created = User.objects.get_or_create(
                username=f"s{i:02d}", defaults={"first_name": first, "last_name": last, "role": User.STUDENT,
                                                "roll_no": f"{9650 + i}", "prn": f"20230164023{i:05d}",
                                                "year_of_study": 2, "department": "Computer Engineering"})
            if created:
                s.set_password("student123")
                s.save()
            students.append(s)

        if teacher.courses_taught.filter(code="CS201").exists():
            self.stdout.write("Demo course already exists - nothing to do.")
            return

        today = timezone.localdate()
        year = today.year
        for name, kind, ay, active in [
            (f"Odd Term {year - 1}", AcademicTerm.ODD, year - 1, False),
            (f"Re-Exam Even {year - 1}", AcademicTerm.REEXAM, year - 1, False),
            (f"Special Exam Odd {year - 1}", AcademicTerm.SPECIAL, year - 1, False),
            (f"Even Term {year}", AcademicTerm.EVEN, year, True),
        ]:
            AcademicTerm.objects.get_or_create(name=name, defaults={"kind": kind, "academic_year": ay, "is_active": active})
        term = AcademicTerm.objects.get(is_active=True)

        text = (Path(settings.BASE_DIR) / "samples" / "data_structures_syllabus.txt").read_text()
        data = syllabus.parse_syllabus(text)
        start = today - timedelta(days=today.weekday()) - timedelta(weeks=5)
        info = {"code": data["code"], "title": data["title"], "department": "Computer Engineering", "year": 2, "semester": 3,
                "division": "A", "academic_year": "2026-27", "credits": Decimal("4"), "lecture_hours": 3, "tutorial_hours": 0,
                "practical_hours": 2, "start_date": start, "end_date": start + timedelta(weeks=14),
                "prerequisites": data["prerequisites"], "syllabus_text": text, "term": term}
        course, summary = build_course(teacher, data, info)
        for s in students:
            Enrollment.objects.get_or_create(course=course, student=s)

        for weekday, hour, kind, room in [(0, 10, "lecture", "LH-201"), (2, 11, "lecture", "LH-201"), (4, 9, "lecture", "LH-201"), (3, 14, "lab", "Lab-3")]:
            TimetableSlot.objects.create(course=course, weekday=weekday, start_time=time(hour), room=room, session_type=kind,
                                         end_time=time(hour + (2 if kind == "lab" else 1)))
        Holiday.objects.create(course=course, date=start + timedelta(weeks=2, days=2), name="Institute holiday")
        sync_timetable(course)

        # deliver past sessions with notes and attendance
        for session in course.sessions.filter(date__lt=today).select_related("plan_item"):
            session.status = ClassSession.COMPLETED
            session.coverage = "full"
            session.delivered_at = timezone.now()
            if session.plan_item:
                session.actual_topics = session.plan_item.title
                session.notes = f"Key points:\n- {session.plan_item.objective}\n- Worked example discussed in class.\n- Practice: see post-class task."
            session.save()
            for s in students:
                weak = s.username in ("s05", "s11")
                status = "A" if rng.random() < (0.35 if weak else 0.08) else "P"
                AttendanceRecord.objects.create(session=session, student=s, status=status)

        # publish and grade first two assignments (one via rubric, one via totals)
        for idx, a in enumerate(course.assignments.filter(kind="assignment").order_by("pk")[:2]):
            a.status = Assignment.PUBLISHED
            a.due_at = timezone.now() - timedelta(days=7 - idx * 3)
            a.save()
            for s in students:
                sub = grading.get_or_create_submission(a, s)
                sub.text = "Answers attached."
                sub.link = "https://colab.research.google.com/"
                sub.status = Submission.TURNED_IN
                sub.submitted_at = a.due_at - timedelta(hours=rng.randint(1, 48))
                sub.save()
                if idx == 0:
                    entries = {}
                    for c in a.rubric.criteria.prefetch_related("levels"):
                        level = rng.choice(list(c.levels.all())[:3])
                        entries[c.pk] = {"level_id": level.pk, "points": None, "comment": ""}
                    grading.apply_rubric_scores(sub, entries, "Good effort.")
                else:
                    grading.set_total_score(sub, Decimal(rng.randint(5, 10)), "Well done.")
        # third assignment published, ungraded, some turned in
        third = course.assignments.filter(kind="assignment").order_by("pk")[2]
        third.status = Assignment.PUBLISHED
        third.due_at = timezone.now() + timedelta(days=5)
        third.save()
        for s in students[:5]:
            sub = grading.get_or_create_submission(third, s)
            sub.text = "My solutions for unit 3."
            sub.status = Submission.TURNED_IN
            sub.submitted_at = timezone.now()
            sub.save()

        # MSE marks
        mse = course.exams.filter(component__kind="MSE").first()
        if mse:
            mse.date = today - timedelta(days=3)
            mse.status = "marked"
            mse.save()
            for s in students:
                skill = rng.uniform(0.45, 0.95)
                for eq in mse.paper.all():
                    value = (eq.marks * Decimal(str(min(1, max(0, rng.gauss(skill, 0.15)))))).quantize(Decimal("0.5"))
                    ExamMark.objects.create(exam_question=eq, student=s, marks=value)

        self.stdout.write(self.style.SUCCESS(
            f"Created {course} with {summary['plan_items']} plan items, {summary['questions']} questions, "
            f"{course.sessions.count()} sessions.\nLogin: teacher / teacher123 · students s01..s12 / student123 · class code {course.join_code}"))
