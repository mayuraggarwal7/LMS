"""Semester computation and promotion against the institution's rules."""

from decimal import Decimal

from django.db import transaction

from classroom.grading import build_gradebook
from courses.models import Course
from delivery.services import attendance_stats
from institution.models import rule_value

from .models import CourseResult, SemesterRecord, StudentProfile


def courses_for(student, semester):
    """LMS courses the student is enrolled in for that semester of their programme."""
    return Course.objects.filter(enrollments__student=student.user, semester=semester).distinct()


@transaction.atomic
def compute_semester(student, semester, academic_year="", term=None):
    """Build/refresh a SemesterRecord from the LMS gradebook and attendance.

    SGPA = sum(grade point x credits) / credits attempted; a course with grade point
    below 4 (F) is a backlog. Detention follows the attendance rule in force.
    """
    record, _ = SemesterRecord.objects.get_or_create(student=student, semester=semester)
    record.courses.all().delete()
    attempted = earned = points = Decimal("0")
    fails = 0
    attendance = []
    for course in courses_for(student, semester):
        row = build_gradebook(course, [student.user])["rows"][0]
        att = attendance_stats(course, [student.user]).get(student.user.pk, {})
        if row["grade_point"] is None:
            continue
        credits = course.credits or Decimal("0")
        CourseResult.objects.create(
            record=record, course=course, course_code=course.code, course_title=course.title[:200],
            credits=credits, grade=row["grade"], grade_point=row["grade_point"], attendance_percent=att.get("percent"),
        )
        attempted += credits
        points += credits * row["grade_point"]
        if row["grade_point"] >= 4:
            earned += credits
        else:
            fails += 1
        if att.get("percent") is not None:
            attendance.append(att["percent"])
    previous = SemesterRecord.objects.filter(student=student, semester__lt=semester).order_by("-semester").first()
    record.credits_attempted = attempted
    record.credits_earned = earned
    record.sgpa = (points / attempted).quantize(Decimal("0.01")) if attempted else None
    record.backlogs = (previous.backlogs if previous else 0) + fails
    record.attendance_percent = Decimal(str(round(sum(attendance) / len(attendance), 1))) if attendance else None
    record.academic_year = academic_year or record.academic_year
    record.term = term or record.term
    record.source = "lms"
    record.result = decide_result(student, record)
    record.save()
    return record


def decide_result(student, record):
    min_att = rule_value("min_attendance_percent", student.program, Decimal("75"))
    max_backlogs = rule_value("max_backlogs_for_promotion", student.program, None)
    if record.attendance_percent is not None and record.attendance_percent < min_att:
        return SemesterRecord.DETAINED
    if record.credits_attempted == 0:
        return SemesterRecord.IN_PROGRESS
    if record.backlogs == 0:
        return SemesterRecord.PASS
    if max_backlogs is not None and record.backlogs > max_backlogs:
        return SemesterRecord.FAIL
    return SemesterRecord.ATKT


def promote(student):
    """Move a student on after their current semester's result. Returns a message."""
    record = student.semesters.filter(semester=student.current_semester).first()
    if record is None or record.result == SemesterRecord.IN_PROGRESS:
        return f"{student}: semester {student.current_semester} result not declared yet."
    if record.result in (SemesterRecord.DETAINED, SemesterRecord.FAIL):
        student.status = StudentProfile.DETAINED if record.result == SemesterRecord.DETAINED else StudentProfile.YEAR_DROP
        student.save(update_fields=["status"])
        return f"{student}: {record.get_result_display().lower()}, not promoted."
    if student.current_semester >= student.program.semesters:
        if record.backlogs == 0:
            student.status = StudentProfile.PASSED_OUT
            student.passed_out_year = int(record.academic_year[:4]) + 1 if record.academic_year[:4].isdigit() else student.expected_passing_year
            student.save(update_fields=["status", "passed_out_year"])
            return f"{student}: passed out with CGPA {student.cgpa()}."
        return f"{student}: final semester complete but {record.backlogs} backlog(s) remain."
    student.current_semester += 1
    student.status = StudentProfile.ACTIVE
    student.save(update_fields=["current_semester", "status"])
    return f"{student}: promoted to semester {student.current_semester}."


def timeline(student):
    """Per-semester cards from entry to the programme's last semester (future ones empty)."""
    records = {r.semester: r for r in student.semesters.prefetch_related("courses")}
    cards = []
    for sem in range(student.entry_semester, student.program.semesters + 1):
        rec = records.get(sem)
        state = "done" if rec and rec.result not in (SemesterRecord.IN_PROGRESS,) else (
            "current" if sem == student.current_semester and student.status == StudentProfile.ACTIVE else
            ("ongoing" if rec else "future"))
        cards.append({"semester": sem, "year": (sem + 1) // 2, "record": rec, "state": state})
    return cards
