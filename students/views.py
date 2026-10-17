from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import User
from biometric.services import presence
from finance.services import student_ledger
from institution.models import Program, rule_value
from institution.views import admin_required, get_institution

from .models import SemesterRecord, StudentProfile
from .services import compute_semester, promote, timeline


class AdmissionForm(forms.Form):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=False)
    program = forms.ModelChoiceField(queryset=Program.objects.none())
    admission_no = forms.CharField(max_length=40)
    roll_no = forms.CharField(max_length=40, required=False)
    prn = forms.CharField(max_length=40, required=False, label="PRN")
    admission_year = forms.IntegerField(min_value=1980, max_value=2100)
    admission_type = forms.ChoiceField(choices=StudentProfile.ADMISSION_TYPES)
    category = forms.ChoiceField(choices=StudentProfile.CATEGORIES)
    division = forms.CharField(max_length=10, required=False)
    qualifying_exam = forms.CharField(max_length=80, required=False)
    qualifying_score = forms.DecimalField(required=False, max_digits=7, decimal_places=2)

    def __init__(self, *args, institution=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["program"].queryset = Program.objects.filter(institution=institution, active=True)

    def clean_admission_no(self):
        value = self.cleaned_data["admission_no"].strip()
        if StudentProfile.objects.filter(admission_no=value).exists():
            raise forms.ValidationError("That admission number is already used.")
        return value

    def clean(self):
        data = super().clean()
        program = data.get("program")
        if program and data.get("admission_type") == StudentProfile.LATERAL and program.duration_years < 2:
            raise forms.ValidationError("Lateral entry needs a programme of at least two years.")
        return data


class SemesterRecordForm(forms.ModelForm):
    class Meta:
        model = SemesterRecord
        fields = ["semester", "academic_year", "sgpa", "credits_attempted", "credits_earned", "attendance_percent",
                  "backlogs", "result", "remarks"]


@admin_required
def student_list(request):
    inst = get_institution()
    qs = StudentProfile.objects.filter(program__institution=inst).select_related("user", "program")
    f = request.GET
    if f.get("program"):
        qs = qs.filter(program_id=f["program"])
    if f.get("year"):
        y = int(f["year"])
        qs = qs.filter(current_semester__in=[2 * y - 1, 2 * y])
    if f.get("status"):
        qs = qs.filter(status=f["status"])
    if f.get("q"):
        q = f["q"]
        qs = qs.filter(Q(user__first_name__icontains=q) | Q(user__last_name__icontains=q) | Q(admission_no__icontains=q)
                       | Q(user__roll_no__icontains=q) | Q(user__prn__icontains=q))
    page = Paginator(qs, 50).get_page(f.get("page"))
    return render(request, "students/list.html", {
        "page": page, "programs": inst.programs.filter(active=True), "statuses": StudentProfile.STATUSES, "filters": f,
        "total": qs.count(),
    })


@admin_required
def admit(request):
    inst = get_institution()
    form = AdmissionForm(request.POST or None, institution=inst, initial={"admission_year": timezone.localdate().year})
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        username = cd["admission_no"].lower().replace(" ", "")
        if User.objects.filter(username=username).exists():
            form.add_error("admission_no", "A user with this admission number already exists.")
        else:
            user = User.objects.create_user(username=username, email=cd["email"], first_name=cd["first_name"],
                                            last_name=cd["last_name"], role=User.STUDENT, roll_no=cd["roll_no"], prn=cd["prn"],
                                            department=cd["program"].department.name if cd["program"].department else "")
            user.set_unusable_password()
            user.save()
            lateral = cd["admission_type"] == StudentProfile.LATERAL
            entry = 3 if lateral else 1
            user.year_of_study = (entry + 1) // 2
            user.save(update_fields=["year_of_study"])
            sp = StudentProfile.objects.create(
                user=user, program=cd["program"], admission_no=cd["admission_no"], admission_year=cd["admission_year"],
                admission_type=cd["admission_type"], category=cd["category"], entry_semester=entry, current_semester=entry,
                division=cd["division"], qualifying_exam=cd["qualifying_exam"], qualifying_score=cd["qualifying_score"])
            messages.success(request, f"{user.display_name} admitted to {sp.program} (semester {entry}). "
                                      "Set a password from the admin site or have them use password reset.")
            return redirect("student_detail", sp.pk)
    lateral_cap = {p.pk: rule_value("lateral_entry_percent", p) for p in Program.objects.filter(institution=inst)}
    return render(request, "students/admit.html", {"form": form, "lateral_cap": lateral_cap})


def _can_view(request, sp):
    u = request.user
    return u.is_staff or u.is_superuser or sp.user_id == u.pk


@login_required
def student_detail(request, student_id):
    sp = get_object_or_404(StudentProfile.objects.select_related("user", "program__department"), pk=student_id)
    if not _can_view(request, sp):
        raise PermissionDenied
    is_admin = request.user.is_staff or request.user.is_superuser
    form = None
    if is_admin:
        form = SemesterRecordForm(request.POST or None, initial={"semester": sp.current_semester})
        if request.method == "POST" and form.is_valid():
            rec = form.save(commit=False)
            SemesterRecord.objects.update_or_create(student=sp, semester=rec.semester, defaults={
                f: getattr(rec, f) for f in SemesterRecordForm.Meta.fields if f != "semester"})
            messages.success(request, f"Semester {rec.semester} record saved.")
            return redirect("student_detail", sp.pk)
    today = timezone.localdate()
    start = today.replace(day=1)
    days = sorted(d for (uid, d) in presence([sp.user], start, today))
    courses = sp.user.enrollments.select_related("course")
    return render(request, "students/detail.html", {
        "sp": sp, "cards": timeline(sp), "cgpa": sp.cgpa(), "credits": sp.total_credits(),
        "ledger": student_ledger(sp), "campus_days": len(days), "courses": courses, "form": form, "is_admin": is_admin,
        "min_attendance": rule_value("min_attendance_percent", sp.program, 75),
        "percent_equivalent": (sp.cgpa() - Decimal("0.75")) * 10 if sp.cgpa() else None,
    })


@login_required
def my_progress(request):
    sp = getattr(request.user, "student_profile", None)
    if sp is None:
        messages.info(request, "Your account is not linked to an admission record yet.")
        return redirect("dashboard")
    return redirect("student_detail", sp.pk)


@admin_required
@require_POST
def compute(request, student_id):
    sp = get_object_or_404(StudentProfile, pk=student_id)
    sem = int(request.POST.get("semester") or sp.current_semester)
    rec = compute_semester(sp, sem, request.POST.get("academic_year", ""))
    if rec.credits_attempted:
        messages.success(request, f"Semester {sem}: SGPA {rec.sgpa}, {rec.get_result_display()}.")
    else:
        messages.warning(request, f"No graded LMS courses found for semester {sem}; enter the result manually.")
    return redirect("student_detail", sp.pk)


@admin_required
@require_POST
def promote_view(request, student_id):
    sp = get_object_or_404(StudentProfile, pk=student_id)
    messages.info(request, promote(sp))
    return redirect("student_detail", sp.pk)


@admin_required
@require_POST
def bulk_promote(request):
    ids = request.POST.getlist("selected")
    results = [promote(sp) for sp in StudentProfile.objects.filter(pk__in=ids)]
    for r in results[:10]:
        messages.info(request, r)
    if len(results) > 10:
        messages.info(request, f"…and {len(results) - 10} more.")
    return redirect(request.POST.get("next") or "student_list")
