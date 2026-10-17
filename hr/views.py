from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import User
from biometric.services import staff_attendance
from courses.models import Course
from courses.terms import current_term, in_term
from institution.views import admin_required, get_institution

from .models import Competency, FacultyCompetency, FacultyProfile, FacultyRating, WorkloadAllocation
from .services import allocate, rating_summary, staffing_metrics, suggest_faculty, workload


class FacultyForm(forms.ModelForm):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=False)

    class Meta:
        model = FacultyProfile
        fields = ["employee_id", "department", "staff_type", "designation", "employment", "qualification", "has_phd",
                  "specialization", "experience_years", "date_of_joining", "biometric_id", "active"]
        widgets = {"date_of_joining": forms.DateInput(attrs={"type": "date"}), "specialization": forms.Textarea(attrs={"rows": 2})}


class AllocationForm(forms.ModelForm):
    class Meta:
        model = WorkloadAllocation
        fields = ["course", "description", "role", "hours_per_week", "division"]


class RatingForm(forms.ModelForm):
    class Meta:
        model = FacultyRating
        fields = ["source", "criterion", "course", "score", "responses", "comment"]
        widgets = {"comment": forms.Textarea(attrs={"rows": 2})}

    def clean_score(self):
        s = self.cleaned_data["score"]
        if not (Decimal("1") <= s <= Decimal("5")):
            raise forms.ValidationError("Score must be between 1 and 5.")
        return s


@admin_required
def faculty_list(request):
    inst = get_institution()
    term = current_term(request)
    qs = FacultyProfile.objects.filter(department__institution=inst).select_related("user", "department")
    f = request.GET
    if f.get("dept"):
        qs = qs.filter(department_id=f["dept"])
    if f.get("type"):
        qs = qs.filter(staff_type=f["type"])
    rows = [{"f": fp, "load": workload(fp, None), "rating": rating_summary(fp)["overall"]} for fp in qs.prefetch_related("allocations", "ratings")]
    if f.get("load") == "over":
        rows = [r for r in rows if r["load"]["status"] == "over"]
    elif f.get("load") == "under":
        rows = [r for r in rows if r["load"]["status"] == "under"]
    return render(request, "hr/faculty_list.html", {
        "rows": rows, "departments": inst.departments.all(), "filters": f, "staffing": staffing_metrics(inst),
        "types": FacultyProfile.STAFF_TYPES, "term": term,
    })


@login_required
def faculty_detail(request, faculty_id):
    fp = get_object_or_404(FacultyProfile.objects.select_related("user", "department"), pk=faculty_id)
    is_admin = request.user.is_staff or request.user.is_superuser
    if not is_admin and fp.user_id != request.user.pk:
        raise PermissionDenied
    alloc_form = AllocationForm(prefix="al")
    rating_form = RatingForm(prefix="rt")
    if request.method == "POST" and is_admin:
        action = request.POST.get("action")
        if action == "allocate":
            alloc_form = AllocationForm(request.POST, prefix="al")
            if alloc_form.is_valid():
                cd = alloc_form.cleaned_data
                a = allocate(fp, cd["course"], cd["role"], cd["hours_per_week"], current_term(request), cd["division"])
                a.description = cd["description"]
                a.save(update_fields=["description"])
                messages.success(request, "Workload allocated.")
                return redirect("faculty_detail", fp.pk)
        elif action == "rate":
            rating_form = RatingForm(request.POST, prefix="rt")
            if rating_form.is_valid():
                r = rating_form.save(commit=False)
                r.faculty = fp
                r.term = current_term(request)
                r.save()
                messages.success(request, "Rating recorded.")
                return redirect("faculty_detail", fp.pk)
        elif action == "competency":
            name = (request.POST.get("name") or "").strip()
            if name:
                comp, _ = Competency.objects.get_or_create(name=name[:120], defaults={"area": request.POST.get("area") or "subject"})
                FacultyCompetency.objects.update_or_create(
                    faculty=fp, competency=comp,
                    defaults={"level": int(request.POST.get("level") or 3), "evidence": request.POST.get("evidence", "")[:200]})
                messages.success(request, f"Competency '{comp.name}' saved.")
            return redirect("faculty_detail", fp.pk)
        elif action == "remove_allocation":
            fp.allocations.filter(pk=request.POST.get("id")).delete()
            return redirect("faculty_detail", fp.pk)
    alloc_form.fields["course"].queryset = Course.objects.filter(archived=False)
    today = timezone.localdate()
    att = staff_attendance([fp.user], today.replace(day=1), today)[0]
    return render(request, "hr/faculty_detail.html", {
        "fp": fp, "load": workload(fp), "allocations": fp.allocations.select_related("course", "term"),
        "competencies": fp.competencies.select_related("competency"), "ratings": fp.ratings.select_related("course")[:20],
        "rating": rating_summary(fp), "alloc_form": alloc_form, "rating_form": rating_form, "is_admin": is_admin,
        "attendance": att, "areas": Competency.AREAS, "payslips": fp.payslips.select_related("run")[:6],
        "courses_taught": Course.objects.filter(teacher=fp.user) | Course.objects.filter(co_teachers=fp.user),
    })


@admin_required
def faculty_edit(request, faculty_id=None):
    inst = get_institution()
    fp = get_object_or_404(FacultyProfile, pk=faculty_id) if faculty_id else None
    initial = {"first_name": fp.user.first_name, "last_name": fp.user.last_name, "email": fp.user.email} if fp else {}
    form = FacultyForm(request.POST or None, instance=fp, initial=initial)
    form.fields["department"].queryset = inst.departments.all()
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        obj = form.save(commit=False)
        if fp is None:
            username = cd["employee_id"].lower().replace(" ", "")
            if User.objects.filter(username=username).exists():
                form.add_error("employee_id", "A user with this id already exists.")
                return render(request, "institution/form.html", {"form": form, "title": "Staff member"})
            user = User.objects.create_user(username=username, role=User.TEACHER)
            user.set_unusable_password()
            obj.user = user
        obj.user.first_name, obj.user.last_name, obj.user.email = cd["first_name"], cd["last_name"], cd["email"]
        obj.user.department = obj.department.name if obj.department else ""
        obj.user.save()
        obj.save()
        messages.success(request, f"{obj.user.display_name} saved.")
        return redirect("faculty_detail", obj.pk)
    return render(request, "institution/form.html", {"form": form, "title": "Staff member"})


@admin_required
def allocation_board(request):
    """Every course in the viewed term with its allocated staff and the best-matched available faculty."""
    term = current_term(request)
    courses = in_term(Course.objects.filter(archived=False), term).select_related("program").prefetch_related(
        "allocations__faculty__user", "units")
    if request.method == "POST":
        course = get_object_or_404(Course, pk=request.POST.get("course"))
        fp = get_object_or_404(FacultyProfile, pk=request.POST.get("faculty"))
        role = request.POST.get("role") or WorkloadAllocation.THEORY
        hours = Decimal(request.POST.get("hours") or course.lecture_hours or 3)
        allocate(fp, course, role, hours, term)
        messages.success(request, f"{fp.user.display_name} allocated to {course.code} ({hours} h/week).")
        return redirect("allocation_board")
    rows = []
    for c in courses:
        allocated = list(c.allocations.all())
        rows.append({"course": c, "allocated": allocated,
                     "suggestions": suggest_faculty(c, None, limit=3),
                     "lab_suggestions": suggest_faculty(c, None, limit=2, staff_type=FacultyProfile.LAB) if c.practical_hours else []})
    return render(request, "hr/allocation_board.html", {"rows": rows, "term": term, "roles": WorkloadAllocation.ROLES})


@login_required
def my_hr(request):
    fp = getattr(request.user, "faculty_profile", None)
    if fp is None:
        messages.info(request, "Your account is not linked to a staff record yet.")
        return redirect("dashboard")
    return redirect("faculty_detail", fp.pk)


@admin_required
@require_POST
def rate_bulk(request):
    """Import student-feedback averages: CSV-like lines 'employee_id, score, responses'."""
    made = 0
    for line in (request.POST.get("rows") or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        fp = FacultyProfile.objects.filter(employee_id=parts[0]).first()
        try:
            score = Decimal(parts[1])
        except Exception:
            continue
        if fp and Decimal("1") <= score <= Decimal("5"):
            FacultyRating.objects.create(faculty=fp, score=score, responses=int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1,
                                         source="students", term=current_term(request))
            made += 1
    messages.success(request, f"{made} feedback rating(s) imported.")
    return redirect("faculty_list")

