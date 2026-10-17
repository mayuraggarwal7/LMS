from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from classroom.models import Assignment, Submission
from courses.models import Course
from courses.terms import current_term, in_term
from delivery.models import ClassSession
from delivery.services import attendance_stats, plan_progress

from .forms import ProfileForm, SignupForm


def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Welcome to CampusFlow!")
        return redirect("dashboard")
    return render(request, "registration/signup.html", {"form": form})


@login_required
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile updated.")
        return redirect("profile")
    return render(request, "accounts/profile.html", {"form": form})


@login_required
def dashboard(request):
    if request.user.is_teacher:
        return teacher_dashboard(request)
    return student_dashboard(request)


def teacher_dashboard(request):
    user = request.user
    term = current_term(request)
    mine = (Course.objects.filter(teacher=user) | Course.objects.filter(co_teachers=user)).distinct()
    courses = in_term(mine, term).filter(archived=False)
    if term is not None:  # a re-exam term also lists courses that hold re-exams in it
        courses = (courses | mine.filter(exams__term=term)).distinct()
    today = timezone.localdate()
    cards = []
    for c in courses:
        progress = plan_progress(c)
        stats = attendance_stats(c)
        cards.append({
            "course": c,
            "progress": progress,
            "students": len(stats),
            "at_risk": sum(1 for s in stats.values() if s["at_risk"]),
            "to_grade": Submission.objects.filter(assignment__course=c, status=Submission.TURNED_IN).count(),
        })
    todays = ClassSession.objects.filter(course__in=courses, date=today).select_related("course", "plan_item").order_by("start_time")
    upcoming = ClassSession.objects.filter(course__in=courses, date__gt=today, status=ClassSession.SCHEDULED).select_related("course", "plan_item")[:6]
    pending_notes = ClassSession.objects.filter(course__in=courses, date__lte=today, status=ClassSession.SCHEDULED).count()
    return render(request, "dashboard_teacher.html", {
        "cards": cards, "todays": todays, "upcoming": upcoming, "pending_notes": pending_notes,
    })


def student_dashboard(request):
    user = request.user
    courses = in_term(Course.objects.filter(enrollments__student=user, archived=False), current_term(request))
    now = timezone.now()
    submitted = set(
        Submission.objects.filter(student=user, status__in=[Submission.TURNED_IN, Submission.RETURNED]).values_list("assignment_id", flat=True)
    )
    todo = [
        a for a in Assignment.objects.filter(course__in=courses, status=Assignment.PUBLISHED).select_related("course").order_by("due_at")
        if a.pk not in submitted
    ]
    cards = []
    for c in courses:
        att = attendance_stats(c, [user]).get(user.pk)
        cards.append({"course": c, "attendance": att, "progress": plan_progress(c)})
    today = timezone.localdate()
    todays = ClassSession.objects.filter(course__in=courses, date=today).select_related("course", "plan_item").order_by("start_time")
    returned = Submission.objects.filter(student=user, status=Submission.RETURNED).select_related("assignment__course").order_by("-graded_at")[:5]
    return render(request, "dashboard_student.html", {
        "cards": cards, "todo": todo, "todays": todays, "returned": returned, "now": now,
    })
