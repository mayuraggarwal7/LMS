"""Workload against norms, competency-based allocation suggestions, ratings, AICTE staffing metrics."""

import re
from datetime import date
from decimal import Decimal

from django.db.models import Avg, Sum

from institution.models import Program, rule_value

from .models import FacultyProfile, WorkloadAllocation

DEFAULT_NORMS = {"workload_professor": Decimal("14"), "workload_associate": Decimal("16"),
                 "workload_assistant": Decimal("18"), "workload_lab_staff": Decimal("36")}
STOP = set("and of the in for to with a an engineering introduction basics fundamentals applied lab laboratory".split())


def norm_hours(faculty):
    key = faculty.workload_rule_key
    return rule_value(key, None, DEFAULT_NORMS.get(key, Decimal("18")))


def workload(faculty, term=None):
    qs = faculty.allocations.all()
    if term is not None:
        qs = qs.filter(term=term)
    teaching = sum((a.hours_per_week for a in qs if a.counts_as_teaching), Decimal("0"))
    other = sum((a.hours_per_week for a in qs if not a.counts_as_teaching), Decimal("0"))
    norm = norm_hours(faculty)
    load = teaching if faculty.staff_type == FacultyProfile.TEACHING else teaching + other
    return {"teaching": teaching, "other": other, "norm": norm, "load": load,
            "percent": float(load / norm * 100) if norm else None,
            "spare": norm - load, "status": "over" if load > norm else ("under" if load < norm * Decimal("0.75") else "ok")}


def _words(text):
    return {w for w in re.findall(r"[a-z][a-z+#]{2,}", (text or "").lower()) if w not in STOP}


def course_keywords(course):
    words = _words(course.title)
    for unit in course.units.all():
        words |= _words(unit.title)
    return words


def suggest_faculty(course, term=None, limit=5, staff_type=FacultyProfile.TEACHING):
    """Rank faculty for a course by competency match (weighted by level) and spare hours."""
    target = course_keywords(course)
    dept_name = (course.department or "").lower()
    ranked = []
    for f in FacultyProfile.objects.filter(active=True, staff_type=staff_type).select_related("user", "department") \
            .prefetch_related("competencies__competency"):
        match = 0.0
        reasons = []
        for fc in f.competencies.all():
            overlap = target & _words(fc.competency.name)
            if overlap:
                match += fc.level * len(overlap)
                reasons.append(f"{fc.competency.name} (L{fc.level})")
        overlap = target & _words(f.specialization)
        if overlap:
            match += len(overlap)
        if f.department and dept_name and (f.department.name.lower() in dept_name or dept_name in f.department.name.lower()):
            match += 1
        load = workload(f, term)
        if load["spare"] <= 0:
            continue
        score = match * 10 + float(load["spare"])
        ranked.append({"faculty": f, "match": match, "reasons": reasons[:3], "load": load, "score": score})
    ranked.sort(key=lambda r: (-r["match"], -r["score"]))
    return ranked[:limit]


def rating_summary(faculty, term=None):
    qs = faculty.ratings.all()
    if term is not None:
        qs = qs.filter(term=term)
    by_source = {row["source"]: row["avg"] for row in qs.values("source").annotate(avg=Avg("score"))}
    total = qs.aggregate(responses=Sum("responses"))["responses"] or 0
    weighted = sum(float(r.score) * r.responses for r in qs)
    return {"by_source": by_source, "overall": round(weighted / total, 2) if total else None, "responses": total}


def staffing_metrics(institution):
    """AICTE staffing indicators: faculty:student ratio, cadre ratio, Ph.D. share."""
    from students.models import StudentProfile

    teaching = FacultyProfile.objects.filter(active=True, staff_type=FacultyProfile.TEACHING,
                                             department__institution=institution)
    counts = {d: teaching.filter(designation=d).count() for d in ("professor", "associate", "assistant")}
    counts["professor"] += teaching.filter(designation="principal").count()
    students = StudentProfile.objects.filter(program__institution=institution, status=StudentProfile.ACTIVE).count()
    year = date.today().year
    sanctioned = sum(p.intake * (min(p.duration_years, max(1, year - p.year_started + (0 if date.today().month < 6 else 1)))
                                 if p.year_started else p.duration_years)
                     for p in Program.objects.filter(institution=institution, active=True, level=Program.UG))
    n = teaching.count()
    required = (Decimal(max(students, sanctioned)) / rule_value("faculty_student_ratio", None, Decimal("20"))).quantize(Decimal("1")) if (students or sanctioned) else Decimal("0")
    return {
        "teaching": n, "lab_staff": FacultyProfile.objects.filter(active=True, staff_type=FacultyProfile.LAB, department__institution=institution).count(),
        "students": students, "sanctioned_strength": sanctioned, "required_faculty": int(required),
        "ratio": round(max(students, sanctioned) / n, 1) if n else None,
        "cadre": counts, "phd_percent": round(teaching.filter(has_phd=True).count() / n * 100, 1) if n else None,
    }


def allocate(faculty, course, role, hours, term=None, division=""):
    allocation = WorkloadAllocation.objects.create(faculty=faculty, course=course, role=role, hours_per_week=hours,
                                                   term=term, division=division)
    if course is not None and role in (WorkloadAllocation.THEORY, WorkloadAllocation.LAB, WorkloadAllocation.TUTORIAL):
        if course.teacher_id != faculty.user_id:
            course.co_teachers.add(faculty.user)
    return allocation

