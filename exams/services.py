"""Assessment framework, question bank generation and question paper builder."""

import random
from decimal import Decimal

from django.db import transaction

from courses.models import BLOOM_SHORT
from courses.planner import match_outcomes

from .models import AssessmentComponent, Exam, ExamQuestion, Question

# (name, kind, weight %, reported-out-of, aggregation, min pass %)
SCHEME_THEORY = [
    ("ISE-1", "ISE", 10, 20, "average", 0),
    ("MSE", "MSE", 20, 30, "average", 0),
    ("ISE-2", "ISE", 10, 20, "average", 0),
    ("ESE", "ESE", 60, 100, "average", 40),
]
SCHEME_WITH_LAB = [
    ("ISE-1", "ISE", 10, 20, "average", 0),
    ("MSE", "MSE", 20, 30, "average", 0),
    ("ISE-2", "ISE", 10, 20, "average", 0),
    ("Term work (lab)", "TW", 10, 25, "average", 40),
    ("ESE", "ESE", 50, 100, "average", 40),
]


def create_default_scheme(course):
    if course.components.exists():
        return list(course.components.all())
    scheme = SCHEME_WITH_LAB if course.practical_hours else SCHEME_THEORY
    return [
        AssessmentComponent.objects.create(
            course=course, name=name, kind=kind, weight=Decimal(weight), max_marks=Decimal(out_of),
            aggregation=agg, min_pass_percent=min_pass, order=i,
        )
        for i, (name, kind, weight, out_of, agg, min_pass) in enumerate(scheme)
    ]


# --- question bank -------------------------------------------------------------

TEMPLATES = {
    1: ["Define {t}.", "List the key characteristics of {t}.", "State the significance of {t}."],
    2: ["Explain {t} with a neat diagram.", "Describe {t} with a suitable example.",
        "Explain the working principle of {t}."],
    3: ["Apply the concept of {t} to solve a suitable engineering problem. Show all steps.",
        "Illustrate {t} with a worked numerical / coding example.", "Demonstrate {t} for a given input and trace each step."],
    4: ["Analyse the advantages and limitations of {t}.", "Compare {t} with {t2}, highlighting when each is preferred.",
        "Analyse the time/space or performance characteristics of {t}."],
    5: ["Justify the use of {t} in a real-world application of your choice.",
        "Critically evaluate {t} against alternative approaches for a given scenario."],
    6: ["Design a solution that uses {t} for a practical problem. State assumptions and justify design choices.",
        "Propose an improvement to {t} and explain how you would validate it."],
}
# (marks, bloom levels to pick from, qtype, difficulty)
BANDS = [
    (Decimal("2"), (1, 2), "short", "easy"),
    (Decimal("5"), (2, 3), "long", "medium"),
    (Decimal("10"), (3, 4, 5, 6), "long", "hard"),
]


def _lc(title):
    return title[0].lower() + title[1:] if title and not title[:2].isupper() else title


@transaction.atomic
def generate_question_bank(course, units=None, use_ai=True):
    """Create template (or AI) questions for each topic. Returns count created."""
    from courses.ai import ai_generate_questions

    outcomes = list(course.outcomes.all())
    units = list(units if units is not None else course.units.prefetch_related("topics"))
    created = 0
    for u_idx, unit in enumerate(units):
        topics = list(unit.topics.all())
        ai_questions = ai_generate_questions(course, unit, count=max(6, len(topics) * 2)) if use_ai else None
        if ai_questions:
            for q in ai_questions:
                topic = next((t for t in topics if t.title.lower() == q["topic"].lower()), None)
                bloom = min(6, max(1, int(q.get("bloom_level") or 2)))
                cos = match_outcomes(f"{unit.title} {q['text']}", outcomes, u_idx, len(units))
                Question.objects.create(
                    course=course, unit=unit, topic=topic, outcome=cos[0] if cos else None, text=q["text"],
                    marks=Decimal(int(q.get("marks") or 5)), bloom_level=bloom, source="ai",
                    qtype="short" if int(q.get("marks") or 5) <= 2 else "long", answer_key=q.get("answer_key", ""),
                    difficulty="easy" if bloom <= 2 else "medium" if bloom <= 4 else "hard",
                )
                created += 1
            continue
        rng = random.Random(unit.pk)
        for t_idx, topic in enumerate(topics):
            cos = match_outcomes(f"{unit.title} {topic.title}", outcomes, u_idx, len(units))
            co = cos[0] if cos else None
            co_bloom = co.bloom_level if co else 3
            other = topics[(t_idx + 1) % len(topics)].title if len(topics) > 1 else unit.title
            for marks, levels, qtype, difficulty in BANDS:
                allowed = [l for l in levels if l <= max(co_bloom, 2) + 1] or [levels[0]]
                bloom = rng.choice(allowed)
                text = rng.choice(TEMPLATES[bloom]).format(t=_lc(topic.title), t2=_lc(other))
                text = text[0].upper() + text[1:]
                Question.objects.create(
                    course=course, unit=unit, topic=topic, outcome=co, text=text, marks=marks, bloom_level=bloom,
                    qtype=qtype, difficulty=difficulty, source="generated",
                    answer_key=f"Marking: key definition/idea, correct {BLOOM_SHORT[bloom].lower()}-level reasoning, "
                               f"example/diagram where relevant. Total {marks} marks.",
                )
                created += 1
    return created


# --- paper builder -------------------------------------------------------------

DEFAULT_BLUEPRINT = {"short": 20, "medium": 40, "long": 40}  # % of marks per question band


def build_paper(exam, blueprint=None, seed=None, avoid_used=True):
    """Assemble a question paper for `exam` from the question bank.

    Distributes marks across the exam's units, then across short (2),
    medium (5) and long (10) question bands according to the blueprint. It
    prefers questions not used in other exams of the course and balances
    Bloom levels / COs. Returns a list of warnings (e.g. bank too small).
    """
    blueprint = blueprint or DEFAULT_BLUEPRINT
    rng = random.Random(seed if seed is not None else exam.pk)
    exam.paper.all().delete()
    units = list(exam.units.all()) or list(exam.course.units.all())
    total = Decimal(exam.total_marks)
    warnings = []

    pool = Question.objects.filter(course=exam.course, unit__in=units)
    used = set()
    if avoid_used:
        used = set(ExamQuestion.objects.filter(exam__course=exam.course).exclude(exam=exam).values_list("question_id", flat=True))

    bands = [("short", Decimal("2"), "Section A - Short answer"), ("medium", Decimal("5"), "Section B"),
             ("long", Decimal("10"), "Section C - Long answer")]
    # small papers (e.g. 20 marks ISE) skip the 10-mark band if needed
    targets = {}
    remaining = total
    for key, marks, _ in bands:
        share = (total * Decimal(blueprint.get(key, 0)) / 100)
        count = int(share // marks)
        targets[key] = count
        remaining -= count * marks
    # absorb leftovers with the largest band that fits, then 2-markers
    for key, marks, _ in sorted(bands, key=lambda b: -b[1]):
        while remaining >= marks:
            targets[key] += 1
            remaining -= marks
    if remaining > 0:
        warnings.append(f"{remaining} marks could not be allocated with 2/5/10-mark questions.")

    order = 0
    qnum = 0
    chosen_ids = set()
    for key, marks, section in bands:
        need = targets[key]
        if not need:
            continue
        candidates = list(pool.filter(marks=marks))
        rng.shuffle(candidates)
        # prefer unused, then spread across units & COs, then higher Bloom for long questions
        candidates.sort(key=lambda q: (q.pk in used, 0))
        picked = []
        unit_cycle = list(units)
        rng.shuffle(unit_cycle)
        ui = 0
        while len(picked) < need and candidates:
            unit = unit_cycle[ui % len(unit_cycle)]
            ui += 1
            match = next((q for q in candidates if q.unit_id == unit.pk and q.pk not in chosen_ids), None)
            if match is None and ui > len(unit_cycle) * 2:
                match = next((q for q in candidates if q.pk not in chosen_ids), None)
            if match is None:
                if ui > len(unit_cycle) * 3:
                    break
                continue
            candidates.remove(match)
            chosen_ids.add(match.pk)
            picked.append(match)
        if len(picked) < need:
            warnings.append(f"Only {len(picked)} of {need} {marks}-mark questions available - add more to the question bank.")
        picked.sort(key=lambda q: (q.unit.number if q.unit else 0, q.bloom_level))
        if key == "short":
            qnum += 1
        for i, q in enumerate(picked):
            order += 1
            if key == "short":
                label = f"Q{qnum}{chr(ord('a') + i)}"
            else:
                qnum += 1
                label = f"Q{qnum}"
            ExamQuestion.objects.create(exam=exam, question=q, section=section, label=label, marks=q.marks, order=order)
    if exam.paper_total != total:
        warnings.append(f"Paper totals {exam.paper_total} marks (target {total}).")
    return warnings


def create_exam(course, component, title, total_marks, units, duration, date=None, blueprint=None):
    exam = Exam.objects.create(
        course=course, component=component, title=title, total_marks=Decimal(total_marks),
        duration_minutes=duration, date=date,
    )
    exam.units.set(units)
    warnings = build_paper(exam, blueprint)
    return exam, warnings


def bloom_distribution(exam):
    dist = {}
    for eq in exam.paper.select_related("question"):
        level = eq.question.bloom_level
        dist[level] = dist.get(level, Decimal("0")) + eq.marks
    return {BLOOM_SHORT[k]: v for k, v in sorted(dist.items())}


def co_distribution(exam):
    dist = {}
    for eq in exam.paper.select_related("question__outcome"):
        code = eq.question.outcome.code if eq.question.outcome else "Unmapped"
        dist[code] = dist.get(code, Decimal("0")) + eq.marks
    return dict(sorted(dist.items()))
