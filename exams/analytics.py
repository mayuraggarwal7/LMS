"""Exam totals, marks distribution and CO attainment."""

from collections import defaultdict
from decimal import Decimal

from django.conf import settings

from .models import ExamAbsence, ExamMark


def exam_totals(exam):
    """{student_id: total marks} for students with any marks; absentees map to None."""
    totals = defaultdict(Decimal)
    for m in ExamMark.objects.filter(exam_question__exam=exam):
        totals[m.student_id] += m.marks
    result = dict(totals)
    for sid in ExamAbsence.objects.filter(exam=exam).values_list("student_id", flat=True):
        result[sid] = None
    return result


def distribute_exam_total(exam, student, total):
    """Score -> questions: spread a total across the paper proportionally to question marks."""
    paper = list(exam.paper.all())
    paper_total = sum((eq.marks for eq in paper), Decimal("0"))
    if not paper_total:
        return
    total = max(Decimal("0"), min(Decimal(total), paper_total))
    ratio = total / paper_total
    assigned = Decimal("0")
    for i, eq in enumerate(paper):
        value = (eq.marks * ratio).quantize(Decimal("0.5")) if i < len(paper) - 1 else total - assigned
        value = max(Decimal("0"), min(value, eq.marks))
        assigned += value
        ExamMark.objects.update_or_create(exam_question=eq, student=student, defaults={"marks": value})


def attainment_level(percent_students):
    for threshold, level in settings.LMS_CO_ATTAINMENT_LEVELS:
        if percent_students >= threshold:
            return level
    return 0


def co_attainment(course):
    """Compute direct CO attainment from exam questions and rubric criteria.

    For each CO and student: earned / possible across all mapped evidence.
    A student attains the CO when that ratio >= the CO target %.
    Attainment level (0-3) is based on % of students attaining, separately for
    internal (ISE/MSE/TW + rubric-graded work) and ESE evidence, then combined
    with LMS_CO_ESE_WEIGHT.
    """
    from classroom.models import CriterionScore

    outcomes = list(course.outcomes.all())
    students = list(course.students.values_list("pk", flat=True))
    # evidence[co_id][bucket][student] = [earned, possible]
    evidence = {o.pk: {"internal": defaultdict(lambda: [Decimal(0), Decimal(0)]),
                       "ese": defaultdict(lambda: [Decimal(0), Decimal(0)])} for o in outcomes}

    marks = ExamMark.objects.filter(exam_question__exam__course=course).select_related(
        "exam_question__question", "exam_question__exam__component"
    )
    for m in marks:
        co_id = m.exam_question.question.outcome_id
        if co_id not in evidence:
            continue
        comp = m.exam_question.exam.component
        bucket = "ese" if comp and comp.kind == "ESE" else "internal"
        cell = evidence[co_id][bucket][m.student_id]
        cell[0] += m.marks
        cell[1] += m.exam_question.marks

    scores = CriterionScore.objects.filter(
        submission__assignment__course=course, submission__score__isnull=False
    ).select_related("criterion")
    for cs in scores:
        co_id = cs.criterion.outcome_id
        if co_id not in evidence:
            continue
        cell = evidence[co_id]["internal"][cs.submission.student_id]
        cell[0] += cs.points
        cell[1] += cs.criterion.max_points

    ese_weight = settings.LMS_CO_ESE_WEIGHT
    report = []
    for o in outcomes:
        row = {"outcome": o}
        for bucket in ("internal", "ese"):
            data = evidence[o.pk][bucket]
            assessed = [sid for sid in students if data.get(sid) and data[sid][1] > 0]
            attained = [sid for sid in assessed if data[sid][0] / data[sid][1] * 100 >= o.target_percent]
            avg = (sum(float(data[s][0] / data[s][1]) for s in assessed) / len(assessed) * 100) if assessed else None
            pct = len(attained) / len(assessed) * 100 if assessed else None
            row[bucket] = {
                "assessed": len(assessed), "attained": len(attained), "percent_students": pct,
                "average": avg, "level": attainment_level(pct) if pct is not None else None,
            }
        levels = [(row["internal"]["level"], 1 - ese_weight), (row["ese"]["level"], ese_weight)]
        present = [(lvl, w) for lvl, w in levels if lvl is not None]
        row["overall"] = round(sum(l * w for l, w in present) / sum(w for _, w in present), 2) if present else None
        report.append(row)
    return report


def student_co_profile(course, student, released_only=True):
    """Per-CO percentage for one student across all evidence (for the student view)."""
    from classroom.models import CriterionScore

    data = defaultdict(lambda: [Decimal(0), Decimal(0)])
    marks = ExamMark.objects.filter(exam_question__exam__course=course, student=student)
    if released_only:
        marks = marks.filter(exam_question__exam__status="marked")
    for m in marks.select_related("exam_question__question"):
        if m.exam_question.question.outcome_id:
            data[m.exam_question.question.outcome_id][0] += m.marks
            data[m.exam_question.question.outcome_id][1] += m.exam_question.marks
    for cs in CriterionScore.objects.filter(submission__assignment__course=course, submission__student=student,
                                            submission__status="returned").select_related("criterion"):
        if cs.criterion.outcome_id:
            data[cs.criterion.outcome_id][0] += cs.points
            data[cs.criterion.outcome_id][1] += cs.criterion.max_points
    profile = []
    for o in course.outcomes.all():
        earned, possible = data.get(o.pk, [0, 0])
        profile.append({"outcome": o, "percent": float(earned / possible * 100) if possible else None})
    return profile


def marks_summary(exam):
    totals = [v for v in exam_totals(exam).values() if v is not None]
    if not totals:
        return None
    totals_f = [float(t) for t in totals]
    total = float(exam.total_marks) or 1
    return {
        "count": len(totals_f), "average": sum(totals_f) / len(totals_f), "highest": max(totals_f), "lowest": min(totals_f),
        "pass_rate": len([t for t in totals_f if t / total >= 0.4]) / len(totals_f) * 100,
    }


def question_analysis(exam):
    """Average % scored per question - flags hard or ambiguous questions."""
    rows = []
    for eq in exam.paper.select_related("question__outcome"):
        values = [float(m.marks) for m in eq.marks_obtained.all()]
        avg = sum(values) / len(values) if values else None
        rows.append({"eq": eq, "average": avg, "percent": (avg / float(eq.marks) * 100) if avg is not None and eq.marks else None})
    return rows

