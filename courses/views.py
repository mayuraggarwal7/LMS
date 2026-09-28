import secrets
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import User
from classroom.models import Assignment
from delivery.models import ClassSession
from delivery.services import attendance_stats, plan_progress, sync_timetable

from . import syllabus as syl
from .access import course_member, course_teacher, teacher_required
from .ai import ai_parse_syllabus
from .forms import (AddStudentsForm, CourseReviewForm, CourseSettingsForm, OutcomeFormSet, PlanItemForm,
                    RubricMetaForm, SyllabusUploadForm)
from .models import Course, Enrollment, PlanItem, Rubric, RubricCriterion, Topic
from .planner import generate_learning_plan, renumber
from .rubrics import TEMPLATES, create_rubric_from_template, ensure_default_levels
from .setup import build_course

DRAFT_KEY = "course_draft"


# --- course creation wizard ------------------------------------------------------

@teacher_required
def course_new(request):
    form = SyllabusUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        text = form.cleaned_data["syllabus_text"] or ""
        file_path = ""
        upload = form.cleaned_data.get("syllabus_file")
        if upload:
            try:
                text = syl.extract_text(upload) + "\n" + text
            except Exception:
                messages.error(request, "Could not read that file. Try a text-based PDF or paste the text instead.")
                return render(request, "courses/course_new.html", {"form": form})
            upload.seek(0)
            file_path = default_storage.save(f"syllabi/{upload.name}", upload)
        data = None
        used_ai = False
        if form.cleaned_data["use_ai"]:
            data = ai_parse_syllabus(text)
            used_ai = data is not None
        if data is None:
            data = syl.parse_syllabus(text)
        info = {k: form.cleaned_data[k] for k in ("year", "semester", "department", "division", "academic_year")}
        for k in ("start_date", "end_date"):
            info[k] = form.cleaned_data[k].isoformat() if form.cleaned_data[k] else ""
        request.session[DRAFT_KEY] = {"data": data, "info": info, "text": text, "file": file_path, "ai": used_ai}
        return redirect("course_review")
    return render(request, "courses/course_new.html", {"form": form})


@teacher_required
def course_review(request):
    draft = request.session.get(DRAFT_KEY) or {"data": {}, "info": {}, "text": "", "file": "", "ai": False}
    data, info = draft["data"], draft["info"]
    if request.method == "POST":
        form = CourseReviewForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            parsed = {
                "outcomes": syl.parse_outcomes_text(cd["outcomes_text"]),
                "units": syl.parse_units_text(cd["units_text"]),
                "textbooks": syl.parse_lines(cd["textbooks_text"]),
                "references": syl.parse_lines(cd["references_text"]),
                "experiments": syl.parse_lines(cd["experiments_text"]),
            }
            if not parsed["units"]:
                form.add_error("units_text", "Add at least one unit, e.g. 'Unit 1: Introduction | 6'.")
            else:
                course_info = {k: cd[k] for k in ("code", "title", "department", "year", "semester", "division",
                                                  "academic_year", "credits", "lecture_hours", "tutorial_hours",
                                                  "practical_hours", "start_date", "end_date", "prerequisites")}
                course_info["department"] = course_info["department"] or ""
                course_info["syllabus_text"] = draft.get("text", "")
                course_info["syllabus_file"] = draft.get("file", "")
                options = {k[4:]: cd[k] for k in cd if k.startswith("opt_")}
                course, summary = build_course(request.user, parsed, course_info, options)
                request.session.pop(DRAFT_KEY, None)
                request.session[f"setup_summary_{course.pk}"] = {k: v for k, v in summary.items()}
                messages.success(request, f"{course.code} is ready. Review the generated plan, then add your timetable.")
                return redirect("course_setup_summary", course.pk)
    else:
        initial = {
            "code": data.get("code", ""), "title": data.get("title", ""),
            "credits": data.get("credits") or 3, "lecture_hours": data.get("lecture_hours") if data.get("lecture_hours") is not None else 3,
            "tutorial_hours": data.get("tutorial_hours") or 0,
            "practical_hours": data.get("practical_hours") if data.get("practical_hours") is not None else (2 if data.get("experiments") else 0),
            "prerequisites": data.get("prerequisites", ""),
            "year": info.get("year", 1), "semester": info.get("semester", 1), "department": info.get("department") or request.user.department,
            "division": info.get("division", ""), "academic_year": info.get("academic_year", ""),
            "start_date": info.get("start_date") or None, "end_date": info.get("end_date") or None,
            **syl.to_editable(data),
        }
        form = CourseReviewForm(initial=initial)
    stats = {
        "outcomes": len(data.get("outcomes", [])), "units": len(data.get("units", [])),
        "topics": sum(len(u.get("topics", [])) for u in data.get("units", [])),
        "hours": sum((u.get("hours") or 0) for u in data.get("units", [])), "experiments": len(data.get("experiments", [])),
    }
    return render(request, "courses/course_review.html", {"form": form, "stats": stats, "used_ai": draft.get("ai"), "has_draft": bool(data)})


@login_required
def course_setup_summary(request, course_id):
    course = get_object_or_404(Course, pk=course_id)
    if not course.is_teacher(request.user):
        return redirect("course_detail", course.pk)
    summary = request.session.get(f"setup_summary_{course.pk}")
    return render(request, "courses/setup_summary.html", {"course": course, "summary": summary, "tab": "overview"})


@login_required
@require_POST
def join_course(request):
    code = (request.POST.get("code") or "").strip().lower()
    course = Course.objects.filter(join_code=code, archived=False).first()
    if not course:
        messages.error(request, "No class found with that code.")
        return redirect("dashboard")
    if course.is_teacher(request.user):
        messages.info(request, "You already teach this class.")
    else:
        Enrollment.objects.get_or_create(course=course, student=request.user)
        messages.success(request, f"Joined {course}.")
    return redirect("course_detail", course.pk)


# --- course pages ----------------------------------------------------------------

@course_member
def course_detail(request, course):
    today = timezone.localdate()
    ctx = {
        "course": course, "tab": "overview",
        "progress": plan_progress(course),
        "outcomes": course.outcomes.all(),
        "upcoming": course.sessions.filter(date__gte=today).exclude(status=ClassSession.CANCELLED).select_related("plan_item")[:5],
        "announcements": course.announcements.select_related("author")[:3],
        "units": course.units.prefetch_related("topics", "outcomes"),
    }
    if request.is_course_teacher:
        ctx["checklist"] = [
            ("Syllabus parsed into units & COs", course.units.exists(), "course_syllabus"),
            ("Learning plan generated", course.plan_items.exists(), "course_plan"),
            ("Weekly timetable added", course.slots.exists(), "timetable"),
            ("Sessions synced to calendar", course.sessions.exists(), "timetable"),
            ("Assessment scheme configured", course.components.exists(), "exam_scheme"),
            ("Students joined", course.enrollments.exists(), "course_people"),
            ("First assignment published", course.assignments.filter(status=Assignment.PUBLISHED).exists(), "classwork"),
        ]
        stats = attendance_stats(course)
        ctx["at_risk"] = [s for s in course.students if stats.get(s.pk, {}).get("at_risk")]
    else:
        ctx["my_attendance"] = attendance_stats(course, [request.user]).get(request.user.pk)
    return render(request, "courses/course_detail.html", ctx)


@course_teacher
def course_settings(request, course):
    form = CourseSettingsForm(request.POST or None, instance=course)
    if request.method == "POST":
        if "reset_code" in request.POST:
            from .models import generate_join_code

            course.join_code = generate_join_code()
            course.save(update_fields=["join_code"])
            messages.success(request, "New class code generated.")
            return redirect("course_settings", course.pk)
        if form.is_valid():
            form.save()
            messages.success(request, "Course settings saved.")
            return redirect("course_settings", course.pk)
    return render(request, "courses/course_settings.html", {"course": course, "form": form, "tab": "settings"})


@course_teacher
@require_POST
def course_delete(request, course):
    if request.POST.get("confirm") == course.code:
        course.delete()
        messages.success(request, "Course deleted.")
        return redirect("dashboard")
    messages.error(request, "Type the course code to confirm deletion.")
    return redirect("course_settings", course.pk)


@course_member
def course_syllabus(request, course):
    formset = None
    if request.is_course_teacher:
        formset = OutcomeFormSet(request.POST or None, instance=course, prefix="co")
        if request.method == "POST" and "save_outcomes" in request.POST and formset.is_valid():
            formset.save()
            messages.success(request, "Course outcomes saved.")
            return redirect("course_syllabus", course.pk)
        if request.method == "POST" and "save_units" in request.POST:
            units = syl.parse_units_text(request.POST.get("units_text", ""))
            _update_units(course, units)
            if request.POST.get("regenerate"):
                generate_learning_plan(course)
                sync_timetable(course)
                messages.success(request, "Units updated, learning plan regenerated and timetable re-synced.")
            else:
                messages.success(request, "Units updated.")
            return redirect("course_syllabus", course.pk)
    units_data = [{"number": u.number, "title": u.title, "hours": u.hours, "topics": [t.title for t in u.topics.all()]}
                  for u in course.units.prefetch_related("topics")]
    return render(request, "courses/syllabus.html", {
        "course": course, "tab": "syllabus", "formset": formset,
        "units": course.units.prefetch_related("topics", "outcomes"),
        "units_text": syl.to_editable({"units": units_data})["units_text"],
        "references": course.references.all(), "experiments": course.experiments.all(),
    })


@transaction.atomic
def _update_units(course, units):
    keep = set()
    for u in units:
        unit, _ = course.units.update_or_create(number=u["number"], defaults={"title": u["title"][:250], "hours": u["hours"] or 0})
        keep.add(unit.pk)
        existing = {t.title.lower(): t for t in unit.topics.all()}
        wanted = []
        for i, title in enumerate(u["topics"]):
            topic = existing.pop(title.lower(), None)
            if topic:
                topic.order = i
                topic.save(update_fields=["order"])
            else:
                topic = Topic.objects.create(unit=unit, title=title[:300], order=i)
            wanted.append(topic)
        for t in existing.values():
            t.delete()
    course.units.exclude(pk__in=keep).delete()


# --- learning plan -------------------------------------------------------------------

@course_member
def course_plan(request, course):
    items = course.plan_items.select_related("unit", "experiment").prefetch_related("outcomes", "sessions")
    unit_filter = request.GET.get("unit")
    if unit_filter:
        items = items.filter(unit__number=unit_filter)
    delivered = set(course.sessions.filter(status=ClassSession.COMPLETED).values_list("plan_item_id", flat=True))
    rows = []
    for item in items:
        sessions = [s for s in item.sessions.all()]
        rows.append({"item": item, "session": sessions[0] if sessions else None, "delivered": item.pk in delivered})
    return render(request, "courses/plan.html", {
        "course": course, "tab": "plan", "rows": rows, "progress": plan_progress(course),
        "units": course.units.all(), "unit_filter": unit_filter,
    })


@course_teacher
def plan_item_edit(request, course, item_id=None):
    item = get_object_or_404(PlanItem, pk=item_id, course=course) if item_id else None
    form = PlanItemForm(request.POST or None, instance=item, course=course)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.course = course
        if not obj.sequence:
            after = request.GET.get("after")
            if after and after.isdigit():
                PlanItem.objects.filter(course=course, sequence__gt=int(after)).update(sequence=F("sequence") + 1)
                obj.sequence = int(after) + 1
            else:
                obj.sequence = course.plan_items.count() + 1
        obj.save()
        form.save_m2m()
        renumber(course)
        if request.POST.get("resync"):
            sync_timetable(course)
        messages.success(request, "Plan item saved.")
        return redirect("course_plan", course.pk)
    return render(request, "courses/plan_item_form.html", {"course": course, "tab": "plan", "form": form, "item": item})


@course_teacher
@require_POST
def plan_item_move(request, course, item_id):
    item = get_object_or_404(PlanItem, pk=item_id, course=course)
    direction = request.POST.get("direction")
    neighbour = None
    if direction == "up":
        neighbour = course.plan_items.filter(sequence__lt=item.sequence).order_by("-sequence").first()
    elif direction == "down":
        neighbour = course.plan_items.filter(sequence__gt=item.sequence).order_by("sequence").first()
    if neighbour:
        item.sequence, neighbour.sequence = neighbour.sequence, item.sequence
        PlanItem.objects.filter(pk=item.pk).update(sequence=item.sequence)
        PlanItem.objects.filter(pk=neighbour.pk).update(sequence=neighbour.sequence)
        sync_timetable(course)
    return redirect(reverse("course_plan", args=[course.pk]) + f"#item-{item.pk}")


@course_teacher
@require_POST
def plan_item_delete(request, course, item_id):
    get_object_or_404(PlanItem, pk=item_id, course=course).delete()
    renumber(course)
    sync_timetable(course)
    messages.success(request, "Plan item removed and timetable re-synced.")
    return redirect("course_plan", course.pk)


@course_teacher
@require_POST
def plan_regenerate(request, course):
    count = generate_learning_plan(course)
    result = sync_timetable(course)
    messages.success(request, f"Learning plan regenerated with {count} sessions." + (
        f" {result['mapped']} mapped onto the timetable." if not result["errors"] else ""))
    return redirect("course_plan", course.pk)


# --- rubrics -------------------------------------------------------------------------

@course_teacher
def rubric_list(request, course):
    rubrics = course.rubrics.prefetch_related("criteria__outcome", "criteria__levels", "assignments")
    return render(request, "courses/rubrics.html", {"course": course, "tab": "rubrics", "rubrics": rubrics, "templates": TEMPLATES})


@course_teacher
@require_POST
def rubric_create(request, course):
    kind = request.POST.get("kind", "blank")
    if kind in TEMPLATES:
        rubric = create_rubric_from_template(course, kind, list(course.outcomes.all()))
    else:
        rubric = Rubric.objects.create(course=course, title="New rubric", kind="custom")
        crit = RubricCriterion.objects.create(rubric=rubric, title="Criterion 1", max_points=Decimal("5"))
        ensure_default_levels(crit)
    return redirect("rubric_edit", course.pk, rubric.pk)


@course_teacher
def rubric_edit(request, course, rubric_id):
    rubric = get_object_or_404(Rubric, pk=rubric_id, course=course)
    meta = RubricMetaForm(request.POST or None, instance=rubric)
    if request.method == "POST":
        if "duplicate" in request.POST:
            copy = rubric.clone(title=f"{rubric.title} (copy)", is_template=True)
            return redirect("rubric_edit", course.pk, copy.pk)
        if "delete" in request.POST:
            rubric.delete()
            messages.success(request, "Rubric deleted.")
            return redirect("rubric_list", course.pk)
        if meta.is_valid():
            meta.save()
            _save_rubric_grid(request, rubric, course)
            if "add_criterion" in request.POST:
                crit = RubricCriterion.objects.create(
                    rubric=rubric, title="New criterion", max_points=Decimal("5"), order=rubric.criteria.count()
                )
                ensure_default_levels(crit)
            messages.success(request, "Rubric saved.")
            return redirect("rubric_edit", course.pk, rubric.pk)
    return render(request, "courses/rubric_edit.html", {
        "course": course, "tab": "rubrics", "rubric": rubric, "meta": meta,
        "criteria": rubric.criteria.prefetch_related("levels"), "outcomes": course.outcomes.all(),
        "graded": rubric.assignments.filter(submissions__criterion_scores__isnull=False).exists(),
    })


def _dec(value, default):
    try:
        return Decimal(str(value).strip())
    except Exception:
        return default


@transaction.atomic
def _save_rubric_grid(request, rubric, course):
    outcomes = {str(o.pk): o for o in course.outcomes.all()}
    for crit in rubric.criteria.prefetch_related("levels"):
        prefix = f"c{crit.pk}"
        if request.POST.get(f"{prefix}-delete"):
            crit.delete()
            continue
        crit.title = request.POST.get(f"{prefix}-title", crit.title)[:200] or crit.title
        crit.description = request.POST.get(f"{prefix}-description", crit.description)
        crit.max_points = _dec(request.POST.get(f"{prefix}-max"), crit.max_points)
        crit.outcome = outcomes.get(request.POST.get(f"{prefix}-outcome"))
        crit.order = int(_dec(request.POST.get(f"{prefix}-order"), Decimal(crit.order)))
        crit.save()
        for level in crit.levels.all():
            lp = f"l{level.pk}"
            level.label = request.POST.get(f"{lp}-label", level.label)[:60] or level.label
            level.points = min(_dec(request.POST.get(f"{lp}-points"), level.points), crit.max_points)
            level.descriptor = request.POST.get(f"{lp}-descriptor", level.descriptor)
            level.save()


# --- people ---------------------------------------------------------------------------

@course_member
def course_people(request, course):
    created = []
    form = None
    if request.is_course_teacher:
        form = AddStudentsForm(request.POST or None)
        if request.method == "POST" and "add_students" in request.POST and form.is_valid():
            added, created = _bulk_add_students(course, form.cleaned_data["rows"])
            messages.success(request, f"{added} student(s) enrolled.")
            if not created:
                return redirect("course_people", course.pk)
            form = AddStudentsForm()
        if request.method == "POST" and "add_teacher" in request.POST:
            teacher = User.objects.filter(username=request.POST.get("username", "").strip(), role=User.TEACHER).first()
            if teacher:
                course.co_teachers.add(teacher)
                messages.success(request, f"{teacher.display_name} added as co-teacher.")
            else:
                messages.error(request, "No teacher account with that username.")
            return redirect("course_people", course.pk)
    stats = attendance_stats(course) if request.is_course_teacher else {}
    students = [{"user": s, "attendance": stats.get(s.pk)} for s in course.students]
    return render(request, "courses/people.html", {
        "course": course, "tab": "people", "students": students, "form": form, "created": created,
    })


def _bulk_add_students(course, text):
    added, created = 0, []
    for line in text.splitlines():
        parts = [p.strip() for p in line.replace("\t", ",").split(",")]
        if not parts or not parts[0]:
            continue
        roll = parts[0]
        name = parts[1] if len(parts) > 1 else ""
        email = parts[2] if len(parts) > 2 else ""
        user = (User.objects.filter(username__iexact=roll).first() or User.objects.filter(roll_no__iexact=roll).first()
                or (User.objects.filter(email__iexact=email).first() if email else None))
        if user is None:
            first, _, last = name.partition(" ")
            password = secrets.token_urlsafe(6)
            user = User.objects.create_user(
                username=roll.lower().replace(" ", ""), email=email, password=password, first_name=first, last_name=last,
                role=User.STUDENT, roll_no=roll, year_of_study=course.year, department=course.department,
            )
            created.append({"username": user.username, "password": password, "name": name})
        _, was_created = Enrollment.objects.get_or_create(course=course, student=user)
        added += int(was_created)
    return added, created


@course_teacher
@require_POST
def remove_student(request, course, user_id):
    Enrollment.objects.filter(course=course, student_id=user_id).delete()
    messages.success(request, "Student removed from the class.")
    return redirect("course_people", course.pk)
