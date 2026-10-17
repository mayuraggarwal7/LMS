"""Standard analytic rubric templates for engineering coursework."""

from decimal import Decimal

from .models import Rubric, RubricCriterion, RubricLevel

LEVELS = [
    ("Excellent", Decimal("1.00")),
    ("Good", Decimal("0.75")),
    ("Satisfactory", Decimal("0.50")),
    ("Needs improvement", Decimal("0.25")),
]

TEMPLATES = {
    "assignment": {
        "title": "Assignment rubric",
        "criteria": [
            ("Conceptual understanding", 3, ["Accurate, complete and insightful explanation of all concepts",
                                             "Mostly accurate with minor gaps", "Partially correct, some misconceptions",
                                             "Major misconceptions or missing"]),
            ("Problem solving / application", 4, ["Correct method and result with clear steps and units",
                                                  "Correct method, minor calculation slips", "Method partly correct",
                                                  "Incorrect or no method shown"]),
            ("Presentation & clarity", 2, ["Neat, well organised, labelled diagrams", "Organised with small lapses",
                                           "Hard to follow in places", "Disorganised / illegible"]),
            ("Originality & timeliness", 1, ["Original work, on time", "Original, slightly late",
                                             "Partially copied or late", "Copied / very late"]),
        ],
    },
    "lab": {
        "title": "Lab / practical rubric",
        "criteria": [
            ("Preparation (pre-lab)", 2, ["Aim, theory and procedure fully prepared", "Mostly prepared",
                                          "Partially prepared", "Not prepared"]),
            ("Execution / implementation", 4, ["Works correctly, handles edge cases, independent", "Works with minor help",
                                               "Partially working", "Not working"]),
            ("Observations & results", 2, ["Complete, accurate readings/output with proper tables/graphs",
                                           "Mostly complete", "Incomplete", "Missing"]),
            ("Analysis & conclusion", 1, ["Insightful analysis linked to theory", "Reasonable conclusion",
                                          "Superficial", "Missing"]),
            ("Viva / oral", 1, ["Answers all questions confidently", "Answers most", "Answers a few", "Cannot answer"]),
        ],
    },
    "project": {
        "title": "Mini-project rubric",
        "criteria": [
            ("Problem definition & requirements", 2, ["Clear, relevant, well-scoped", "Clear but broad", "Vague", "Missing"]),
            ("Design & methodology", 3, ["Sound design with justified choices", "Reasonable design",
                                         "Weak justification", "No design"]),
            ("Implementation", 3, ["Complete, well engineered and tested", "Mostly complete", "Partial", "Not functional"]),
            ("Results & validation", 2, ["Thorough testing with metrics", "Some testing", "Minimal", "None"]),
            ("Documentation & presentation", 2, ["Professional report and demo", "Good", "Adequate", "Poor"]),
            ("Teamwork & contribution", 1, ["Excellent collaboration, clear roles", "Good", "Uneven", "Poor"]),
        ],
    },
    "presentation": {
        "title": "Presentation / seminar rubric",
        "criteria": [
            ("Content & technical depth", 4, ["Accurate, deep, well researched", "Accurate, moderate depth",
                                              "Surface level", "Inaccurate"]),
            ("Organisation", 2, ["Logical flow with clear takeaways", "Mostly logical", "Some jumps", "Disorganised"]),
            ("Delivery & visuals", 2, ["Confident, engaging, clear slides", "Clear", "Reads slides", "Unclear"]),
            ("Q&A handling", 2, ["Handles all questions well", "Most questions", "Few questions", "Cannot respond"]),
        ],
    },
}


def create_rubric_from_template(course, kind, outcomes=None, title=None, is_template=True, scale=1):
    spec = TEMPLATES[kind]
    rubric = Rubric.objects.create(
        course=course, title=title or spec["title"], kind=kind, is_template=is_template,
        description="Analytic rubric; each criterion scored on four performance levels.",
    )
    outcomes = list(outcomes or [])
    for i, (name, points, descriptors) in enumerate(spec["criteria"]):
        max_points = Decimal(points) * Decimal(scale)
        crit = RubricCriterion.objects.create(
            rubric=rubric, order=i, title=name, max_points=max_points,
            outcome=outcomes[i % len(outcomes)] if outcomes else None,
        )
        for j, ((label, factor), descriptor) in enumerate(zip(LEVELS, descriptors)):
            RubricLevel.objects.create(
                criterion=crit, order=j, label=label, points=(max_points * factor).quantize(Decimal("0.01")),
                descriptor=descriptor,
            )
    return rubric


def ensure_default_levels(criterion):
    if not criterion.levels.exists():
        for j, (label, factor) in enumerate(LEVELS):
            RubricLevel.objects.create(
                criterion=criterion, order=j, label=label,
                points=(criterion.max_points * factor).quantize(Decimal("0.01")),
            )
