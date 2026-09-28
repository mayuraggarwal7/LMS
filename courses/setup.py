"""Course auto-configuration from a reviewed syllabus.

`build_course` creates the course and, depending on the options, the learning
plan, assessment scheme (ISE/MSE/TW/ESE), rubric library, question bank,
draft assignments per unit and draft MSE/ESE papers - so a teacher starts with
a fully structured course they only need to adjust.
"""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction

from .models import Course, CourseOutcome, Experiment, Reference, Topic, Unit
from .planner import generate_learning_plan, infer_bloom
from .rubrics import create_rubric_from_template

DEFAULT_OPTIONS = {
    "plan": True, "scheme": True, "rubrics": True, "questions": True, "assignments": True, "exams": True,
}


@transaction.atomic
def build_course(teacher, data, info, options=None):
    """data: parsed syllabus dict; info: dict of Course field values; options: which artefacts to generate."""
    options = {**DEFAULT_OPTIONS, **(options or {})}
    course = Course.objects.create(teacher=teacher, **info)
    summary = {"units": 0, "topics": 0, "outcomes": 0, "plan_items": 0, "rubrics": 0, "questions": 0,
               "assignments": 0, "exams": 0, "warnings": []}

    for i, o in enumerate(data.get("outcomes", [])):
        CourseOutcome.objects.create(
            course=course, code=o["code"], description=o["description"], order=i,
            bloom_level=infer_bloom(o["description"]),
        )
        summary["outcomes"] += 1
    for u in data.get("units", []):
        unit = Unit.objects.create(course=course, number=u["number"], title=u["title"][:250], hours=u.get("hours") or 0)
        for j, t in enumerate(u.get("topics", [])):
            Topic.objects.create(unit=unit, title=t[:300], order=j)
            summary["topics"] += 1
        summary["units"] += 1
    for text in data.get("textbooks", []):
        Reference.objects.create(course=course, kind=Reference.TEXTBOOK, text=text)
    for text in data.get("references", []):
        kind = Reference.ONLINE if any(k in text.lower() for k in ("http", "nptel", "swayam", "coursera", "www")) else Reference.REFERENCE
        Reference.objects.create(course=course, kind=kind, text=text)
    for i, text in enumerate(data.get("experiments", []), start=1):
        Experiment.objects.create(course=course, number=i, title=text)

    configure_course(course, options, summary)
    return course, summary


def configure_course(course, options, summary):
    from classroom.models import Assignment
    from exams.services import create_default_scheme, create_exam, generate_question_bank

    outcomes = list(course.outcomes.all())
    if options.get("plan"):
        summary["plan_items"] = generate_learning_plan(course)

    components = create_default_scheme(course) if options.get("scheme") else list(course.components.all())
    by_name = {c.name: c for c in components}

    templates = {}
    if options.get("rubrics"):
        for kind in ("assignment", "lab", "project", "presentation"):
            if kind == "lab" and not course.practical_hours and not course.experiments.exists():
                continue
            templates[kind] = create_rubric_from_template(course, kind, outcomes)
            summary["rubrics"] += 1

    if options.get("questions"):
        summary["questions"] = generate_question_bank(course)

    units = list(course.units.all())
    half = max(1, (len(units) + 1) // 2)
    if options.get("assignments") and units:
        for i, unit in enumerate(units):
            comp = by_name.get("ISE-1") if i < half else by_name.get("ISE-2")
            unit_cos = list(unit.outcomes.all()) or outcomes[:1]
            rubric = templates.get("assignment")
            if rubric:
                rubric = rubric.clone(title=f"Assignment {i + 1} rubric", outcomes=unit_cos)
            topics = ", ".join(t.title for t in unit.topics.all()[:6])
            a = Assignment.objects.create(
                course=course, title=f"Assignment {i + 1}: {unit.title}"[:250], kind="assignment", unit=unit,
                component=comp, rubric=rubric, max_points=Decimal("10"),
                instructions=(
                    f"Answer the following based on Unit {unit.number} ({unit.title}).\n"
                    f"Topics covered: {topics}.\n\n"
                    "1. Explain the key concepts with neat diagrams.\n"
                    "2. Solve at least two application problems (numerical / code) and show every step.\n"
                    "3. Relate one concept to a real engineering application.\n\n"
                    "Submit a PDF scan or a link (Colab / GitHub / Drive)."
                ),
            )
            a.outcomes.set(unit_cos)
            summary["assignments"] += 1
        tw = next((c for c in components if c.kind == "TW"), None)
        if course.experiments.exists():
            lab_rubric = templates.get("lab")
            if lab_rubric:
                lab_rubric = lab_rubric.clone(title="Lab journal rubric", outcomes=outcomes)
            a = Assignment.objects.create(
                course=course, title="Lab journal (all experiments)", kind="lab", component=tw, rubric=lab_rubric,
                max_points=Decimal("25"),
                instructions="Maintain the lab journal: aim, theory, procedure/code, observations, result, conclusion, viva answers for each experiment.",
            )
            a.outcomes.set(outcomes)
            summary["assignments"] += 1
        if course.year >= 3 and templates.get("project"):
            a = Assignment.objects.create(
                course=course, title="Mini project", kind="project", component=by_name.get("ISE-2"),
                rubric=templates["project"].clone(title="Mini project rubric", outcomes=outcomes), max_points=Decimal("20"),
                instructions="Teams of 3-4: identify a real problem in this course's domain, design, implement and demo a solution. Submit report + repository link.",
            )
            a.outcomes.set(outcomes)
            summary["assignments"] += 1

    if options.get("exams") and units:
        mse = by_name.get("MSE")
        ese = next((c for c in components if c.kind == "ESE"), None)
        start = course.start_date
        if mse:
            exam, warnings = create_exam(
                course, mse, "Mid-semester examination", mse.max_marks, units[:half], 60,
                date=start + timedelta(weeks=8) if start else None,
                blueprint={"short": 20, "medium": 50, "long": 30},
            )
            summary["exams"] += 1
            summary["warnings"] += [f"MSE: {w}" for w in warnings]
        if ese:
            exam, warnings = create_exam(
                course, ese, "End-semester examination", ese.max_marks, units, 180,
                date=course.end_date + timedelta(days=10) if course.end_date else None,
            )
            summary["exams"] += 1
            summary["warnings"] += [f"ESE: {w}" for w in warnings]
    return summary

