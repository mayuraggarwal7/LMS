from functools import wraps

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from finance.services import institution_ledger
from hr.services import staffing_metrics
from students.models import StudentProfile

from .models import RULE_KEYS, Department, Institution, Program, Regulation, Rule, applicable_rules, compliance


def admin_required(view):
    """Super admins: Django staff or superusers."""

    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def get_institution():
    inst = Institution.current()
    if inst is None:
        inst = Institution.objects.create(name="My Engineering College")
    return inst


class InstitutionForm(forms.ModelForm):
    class Meta:
        model = Institution
        fields = ["name", "short_name", "address", "city", "established", "management", "affiliating_university",
                  "autonomous_since", "aicte_id", "naac_grade", "website"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2})}


class ProgramForm(forms.ModelForm):
    class Meta:
        model = Program
        fields = ["name", "code", "department", "level", "degree", "duration_years", "intake", "year_started",
                  "nba_accredited", "nba_valid_till", "total_credits", "active"]


class RuleForm(forms.ModelForm):
    class Meta:
        model = Rule
        fields = ["regulation", "key", "program", "value", "text", "note"]


class RegulationForm(forms.ModelForm):
    class Meta:
        model = Regulation
        fields = ["scope", "name", "effective_from", "reference", "active"]


def program_rows(inst):
    rows = []
    counts = {r["program"]: r for r in StudentProfile.objects.filter(program__institution=inst).values("program").annotate(
        active=Count("pk", filter=Q(status=StudentProfile.ACTIVE)), passed=Count("pk", filter=Q(status=StudentProfile.PASSED_OUT)))}
    for p in inst.programs.select_related("department"):
        c = counts.get(p.pk, {})
        rows.append({"program": p, "active": c.get("active", 0), "passed": c.get("passed", 0),
                     "capacity": p.intake * p.duration_years})
    return rows


@admin_required
def console(request):
    inst = get_institution()
    return render(request, "institution/console.html", {
        "inst": inst, "programs": program_rows(inst), "staffing": staffing_metrics(inst),
        "issues": compliance(inst), "ledger": institution_ledger(), "departments": inst.departments.select_related("hod"),
    })


@admin_required
def institution_edit(request):
    inst = get_institution()
    form = InstitutionForm(request.POST or None, instance=inst)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Institution details saved.")
        return redirect("admin_console")
    return render(request, "institution/form.html", {"form": form, "title": "Institution details"})


@admin_required
def program_edit(request, program_id=None):
    inst = get_institution()
    program = get_object_or_404(Program, pk=program_id, institution=inst) if program_id else None
    form = ProgramForm(request.POST or None, instance=program)
    form.fields["department"].queryset = inst.departments.all()
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.institution = inst
        obj.save()
        messages.success(request, f"{obj} saved.")
        return redirect("program_detail", obj.pk)
    return render(request, "institution/form.html", {"form": form, "title": "Programme / branch"})


@admin_required
def program_detail(request, program_id):
    inst = get_institution()
    program = get_object_or_404(Program, pk=program_id, institution=inst)
    effective = []
    for key, (label, unit, _) in RULE_KEYS.items():
        rules = applicable_rules(key, program, inst)
        if rules:
            effective.append({"key": key, "label": label, "unit": unit, "rule": rules[0], "overridden": rules[1:]})
    years = []
    for y in range(1, program.duration_years + 1):
        qs = program.students.filter(status=StudentProfile.ACTIVE, current_semester__in=[2 * y - 1, 2 * y])
        years.append({"year": y, "count": qs.count(), "lateral": qs.filter(admission_type=StudentProfile.LATERAL).count()})
    return render(request, "institution/program_detail.html", {
        "inst": inst, "program": program, "effective": effective, "years": years,
        "fees": program.fee_structures.prefetch_related("lines__head"),
        "courses": program.courses.all()[:50],
    })


@admin_required
def rules(request):
    inst = get_institution()
    rule_form = RuleForm(request.POST if "add_rule" in request.POST else None)
    reg_form = RegulationForm(request.POST if "add_regulation" in request.POST else None)
    rule_form.fields["regulation"].queryset = inst.regulations.all()
    rule_form.fields["program"].queryset = inst.programs.all()
    if request.method == "POST":
        if "add_rule" in request.POST and rule_form.is_valid():
            rule_form.save()
            messages.success(request, "Rule added.")
            return redirect("rules")
        if "add_regulation" in request.POST and reg_form.is_valid():
            reg = reg_form.save(commit=False)
            reg.institution = inst
            reg.save()
            messages.success(request, "Regulation added.")
            return redirect("rules")
        if "delete_rule" in request.POST:
            Rule.objects.filter(pk=request.POST.get("delete_rule"), regulation__institution=inst).delete()
            return redirect("rules")
    regs = inst.regulations.prefetch_related("rules__program")
    matrix = []
    for key, (label, unit, direction) in RULE_KEYS.items():
        cells = {scope: [r for reg in regs if reg.scope == scope for r in reg.rules.all() if r.key == key]
                 for scope, _ in Regulation.SCOPES}
        if any(cells.values()):
            matrix.append({"key": key, "label": label, "unit": unit, "direction": direction, "cells": cells})
    return render(request, "institution/rules.html", {
        "inst": inst, "regulations": regs, "matrix": matrix, "scopes": Regulation.SCOPES,
        "rule_form": rule_form, "reg_form": reg_form, "issues": compliance(inst),
    })


@admin_required
def aicte_report(request):
    """A one-page AICTE compliance view: intake, staffing norms and rule conformity."""
    inst = get_institution()
    staffing = staffing_metrics(inst)
    checks = []
    fsr = applicable_rules("faculty_student_ratio", None, inst)
    if fsr and staffing["ratio"] is not None:
        limit = float(fsr[0].value)
        checks.append({"name": "Faculty : student ratio", "norm": f"1:{fsr[0].display_value}", "actual": f"1:{staffing['ratio']}",
                       "ok": staffing["ratio"] <= limit,
                       "detail": f"{staffing['teaching']} teaching faculty for {max(staffing['students'], staffing['sanctioned_strength'])} students "
                                 f"(sanctioned strength {staffing['sanctioned_strength']}); {staffing['required_faculty']} needed."})
    cadre = applicable_rules("cadre_ratio", None, inst)
    if cadre:
        c = staffing["cadre"]
        checks.append({"name": "Cadre ratio Prof : Assoc : Asst", "norm": cadre[0].display_value,
                       "actual": f"{c['professor']}:{c['associate']}:{c['assistant']}",
                       "ok": cadre_ok(c, cadre[0].text), "detail": "Required professors and associates per assistant professors, per AICTE."})
    if staffing["phd_percent"] is not None:
        checks.append({"name": "Faculty with Ph.D.", "norm": "reported", "actual": f"{staffing['phd_percent']}%", "ok": None,
                       "detail": "Shown for NBA/NIRF reporting."})
    for p in inst.programs.filter(active=True):
        admitted = p.students.filter(admission_year=max(p.students.values_list("admission_year", flat=True) or [0])).exclude(
            admission_type=StudentProfile.LATERAL).exclude(admission_type=StudentProfile.TFWS).count() if p.students.exists() else 0
        if admitted:
            checks.append({"name": f"Intake {p.code}", "norm": f"≤ {p.intake}", "actual": str(admitted), "ok": admitted <= p.intake,
                           "detail": "Regular admissions in the latest batch (lateral entry and TFWS are supernumerary)."})
    for issue in compliance(inst):
        checks.append({"name": issue["rule"].label, "norm": issue["aicte"].display_value, "actual": issue["rule"].display_value,
                       "ok": False, "detail": issue["message"]})
    return render(request, "institution/aicte_report.html", {"inst": inst, "checks": checks, "staffing": staffing,
                                                             "programs": program_rows(inst)})


def cadre_ok(counts, ratio_text):
    try:
        p, a, s = (int(x) for x in ratio_text.split(":"))
    except (ValueError, AttributeError):
        return None
    asst = counts["assistant"]
    if not asst:
        return None
    need_prof = asst * p / s
    need_assoc = asst * a / s
    return counts["professor"] >= need_prof and counts["associate"] >= need_assoc


@admin_required
def departments(request):
    inst = get_institution()
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        code = request.POST.get("code", "").strip().upper()
        if name and code:
            Department.objects.get_or_create(institution=inst, code=code, defaults={"name": name})
            messages.success(request, f"Department {name} added.")
        return redirect("departments")
    rows = []
    for d in inst.departments.select_related("hod").prefetch_related("staff", "programs"):
        staff = list(d.staff.all())
        rows.append({"dept": d, "teaching": sum(1 for s in staff if s.staff_type == "teaching"),
                     "lab": sum(1 for s in staff if s.staff_type != "teaching"),
                     "phd": sum(1 for s in staff if s.has_phd), "programs": list(d.programs.all())})
    return render(request, "institution/departments.html", {"inst": inst, "rows": rows})
