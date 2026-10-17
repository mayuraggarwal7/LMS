"""Grading engine.

Rubric <-> score mapping works both ways:

* rubric -> score: picking levels/points per criterion computes the
  submission score, scaled to the assignment's max points.
* score -> rubric: entering (or importing) only a total distributes it across
  the criteria proportionally and snaps each to the nearest performance level,
  so CO attainment still has criterion-level evidence.

The gradebook rolls item scores up into assessment components (ISE / MSE /
TW / ESE) and a weighted final percentage + grade on a 10-point scale.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from .models import Assignment, CriterionScore, Submission

TWOPLACES = Decimal("0.01")

GRADE_SCALE = [  # (min %, letter, grade point) - 10 point scale used by most Indian universities
    (90, "O", 10), (80, "A+", 9), (70, "A", 8), (60, "B+", 7), (50, "B", 6), (45, "C", 5), (40, "P", 4), (0, "F", 0),
]


def q(value):
    return Decimal(value).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def letter_grade(percent):
    if percent is None:
        return ("-", None)
    for minimum, letter, gp in GRADE_SCALE:
        if percent >= minimum:
            return (letter, gp)
    return ("F", 0)


# --- rubric -> score -----------------------------------------------------------

def apply_rubric_scores(submission, entries, feedback=None, grader_return=True):
    """entries: {criterion_id: {"level_id": int|None, "points": Decimal|None, "comment": str}}"""
    assignment = submission.assignment
    rubric = assignment.rubric
    if rubric is None:
        raise ValueError("Assignment has no rubric")
    for crit in rubric.criteria.prefetch_related("levels"):
        entry = entries.get(crit.pk)
        if not entry:
            continue
        level = None
        points = entry.get("points")
        if entry.get("level_id"):
            level = next((l for l in crit.levels.all() if l.pk == int(entry["level_id"])), None)
        if points is None and level is not None:
            points = level.points
        if points is None:
            continue
        points = max(Decimal("0"), min(Decimal(points), crit.max_points))
        if level is None:
            level = nearest_level(crit, points)
        CriterionScore.objects.update_or_create(
            submission=submission, criterion=crit,
            defaults={"level": level, "points": points, "comment": entry.get("comment", "")[:300], "derived": False},
        )
    submission.score = score_from_criteria(submission)
    submission.score_source = "rubric"
    _finish(submission, feedback, grader_return)
    return submission


def score_from_criteria(submission):
    rubric = submission.assignment.rubric
    total = rubric.total_points if rubric else Decimal("0")
    if not total:
        return None
    earned = sum((cs.points for cs in submission.criterion_scores.all()), Decimal("0"))
    return q(earned / total * submission.assignment.max_points)


def nearest_level(criterion, points):
    levels = list(criterion.levels.all())
    if not levels:
        return None
    return min(levels, key=lambda l: abs(l.points - points))


# --- score -> rubric -----------------------------------------------------------

def set_total_score(submission, score, feedback=None, grader_return=True):
    """Record a total score and distribute it across rubric criteria."""
    assignment = submission.assignment
    score = max(Decimal("0"), min(Decimal(score), assignment.max_points))
    submission.score = q(score)
    submission.score_source = "total"
    distribute_to_criteria(submission)
    _finish(submission, feedback, grader_return)
    return submission


def distribute_to_criteria(submission):
    rubric = submission.assignment.rubric
    if rubric is None or submission.score is None or not submission.assignment.max_points:
        return
    ratio = Decimal(submission.score) / submission.assignment.max_points
    for crit in rubric.criteria.prefetch_related("levels"):
        points = q(crit.max_points * ratio)
        CriterionScore.objects.update_or_create(
            submission=submission, criterion=crit,
            defaults={"level": nearest_level(crit, points), "points": points, "derived": True},
        )


def _finish(submission, feedback, grader_return):
    if feedback is not None:
        submission.feedback = feedback
    submission.graded_at = timezone.now()
    if grader_return:
        submission.status = Submission.RETURNED
    submission.save()


def get_or_create_submission(assignment, student):
    sub, _ = Submission.objects.get_or_create(assignment=assignment, student=student)
    return sub


# --- gradebook -----------------------------------------------------------------

def component_items(component, released_only=False):
    """All gradable items (published assignments + exams) that feed a component.

    released_only restricts exams to those whose marks were released (student views).
    """
    items = []
    for a in component.assignments.filter(status=Assignment.PUBLISHED).order_by("due_at", "pk"):
        items.append(("assignment", a))
    exams = component.exams.filter(purpose="regular").order_by("date", "pk")
    if released_only:
        exams = exams.filter(status="marked")
    for e in exams:
        items.append(("exam", e))
    return items


def _aggregate(component, percents):
    percents = [p for p in percents if p is not None]
    if not percents:
        return None
    if component.aggregation == "best" and component.best_of:
        percents = sorted(percents, reverse=True)[: component.best_of]
    return sum(percents) / len(percents)


def apply_retakes(original, scores, maximum, released_only=False):
    """Fold re-exam / additional-assessment results into an item's scores (in place).

    Each retake is scaled to the original item's maximum. Policy 'replace' uses the retake
    score; 'best' keeps the better of the two. Cells are tagged so reports can show it.
    """
    from exams.analytics import exam_totals

    for retake in original.retakes.all().order_by("date", "pk"):
        if released_only and retake.status != "marked":
            continue
        candidate_ids = set(retake.candidates.values_list("pk", flat=True))
        total = float(retake.total_marks) or 1
        for sid, value in exam_totals(retake).items():
            if sid not in candidate_ids or value is None:
                continue
            new_pct = float(value) / total * 100
            old = scores.get(sid)
            old_pct = old["percent"] if old and old["percent"] is not None else None
            if retake.policy == "best" and old_pct is not None and old_pct >= new_pct:
                continue
            scores[sid] = {
                "score": q(Decimal(new_pct) / 100 * Decimal(maximum)), "percent": new_pct,
                "retake": "RE" if retake.purpose == "reexam" else "ADD", "original": old["score"] if old else None,
            }


def build_gradebook(course, students=None, released_only=False):
    """Return a dict describing every student's standing in the course.

    released_only=True (student-facing) ignores unreturned grades and unreleased exam marks.
    """
    from exams.analytics import exam_totals

    components = list(course.components.all())
    students = list(students if students is not None else course.students)
    columns = []
    item_scores = {}  # (kind, pk) -> {student_id: percent}
    for comp in components:
        items = component_items(comp, released_only)
        cols = []
        for kind, obj in items:
            key = (kind, obj.pk)
            if kind == "assignment":
                subs = Submission.objects.filter(assignment=obj, score__isnull=False)
                if released_only:
                    subs = subs.filter(status=Submission.RETURNED)
                item_scores[key] = {
                    s.student_id: {"score": s.score, "percent": float(s.score) / float(obj.max_points) * 100 if obj.max_points else None}
                    for s in subs
                }
                maximum = obj.max_points
            else:
                totals = exam_totals(obj)
                item_scores[key] = {
                    sid: {"score": v, "percent": float(v) / float(obj.total_marks) * 100 if obj.total_marks and v is not None else (0.0 if v is None else None)}
                    for sid, v in totals.items()
                }
                maximum = obj.total_marks
            apply_retakes(obj, item_scores[key], maximum, released_only)
            cols.append({"kind": kind, "obj": obj, "max": maximum})
        columns.append({"component": comp, "items": cols})

    rows = []
    for student in students:
        row = {"student": student, "components": [], "final_percent": None, "grade": "-", "grade_point": None, "failed_components": []}
        weighted, weight_used = 0.0, 0.0
        for col in columns:
            comp = col["component"]
            cells, percents = [], []
            for item in col["items"]:
                cell = item_scores[(item["kind"], item["obj"].pk)].get(student.pk)
                cells.append(cell)
                percents.append(cell["percent"] if cell else None)
            pct = _aggregate(comp, percents)
            marks = q(Decimal(pct) / 100 * comp.max_marks) if pct is not None else None
            row["components"].append({"component": comp, "cells": cells, "percent": pct, "marks": marks})
            if pct is not None:
                weighted += pct * float(comp.weight)
                weight_used += float(comp.weight)
                if comp.min_pass_percent and pct < comp.min_pass_percent:
                    row["failed_components"].append(comp.name)
        if weight_used:
            row["final_percent"] = weighted / weight_used
            row["weight_covered"] = weight_used
            letter, gp = letter_grade(row["final_percent"])
            if row["failed_components"]:
                letter, gp = "F", 0
            row["grade"], row["grade_point"] = letter, gp
        rows.append(row)
    total_weight = sum(float(c.weight) for c in components)
    return {"columns": columns, "rows": rows, "total_weight": total_weight}
