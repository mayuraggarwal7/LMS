from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from institution.views import admin_required, get_institution

from .models import Deadline, DeadlineAck, Notification
from .services import (course_done, deadline_courses, deadline_report, deadline_users, open_counts, refresh)


class DeadlineForm(forms.ModelForm):
    class Meta:
        model = Deadline
        fields = ["title", "kind", "component_kind", "due_at", "remind_days", "audience", "department", "program", "year",
                  "term", "description", "active"]
        widgets = {"due_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
                   "description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, institution=None, **kwargs):
        super().__init__(*args, **kwargs)
        if institution is not None:
            self.fields["department"].queryset = institution.departments.all()
            self.fields["program"].queryset = institution.programs.all()
        self.fields["year"].widget = forms.Select(choices=[("", "All years"), (1, "First year"), (2, "Second year"),
                                                          (3, "Third year"), (4, "Final year")])

    def clean(self):
        data = super().clean()
        kind = data.get("kind")
        if kind in Deadline.AUTO_KINDS and data.get("audience") != Deadline.TEACHERS:
            self.add_error("audience", "Automatically checked tasks apply to the teachers of matching courses.")
        if kind != Deadline.NOTICE and not data.get("due_at"):
            self.add_error("due_at", "Set the date and time this is due.")
        return data


@login_required
def center(request):
    user = request.user
    if request.method == "POST":
        action = request.POST.get("action")
        qs = Notification.objects.filter(user=user)
        if action == "read_all":
            qs.filter(read_at__isnull=True).update(read_at=timezone.now())
        elif action == "read":
            qs.filter(pk=request.POST.get("id"), read_at__isnull=True).update(read_at=timezone.now())
        elif action == "dismiss":
            qs.filter(pk=request.POST.get("id"), actionable=False).update(resolved_at=timezone.now(), read_at=timezone.now())
        elif action == "ack":
            d = get_object_or_404(Deadline, pk=request.POST.get("deadline"), active=True)
            if d.is_auto:
                messages.info(request, "This one is checked automatically - it clears once the work is done.")
            elif deadline_users(d).filter(pk=user.pk).exists():
                DeadlineAck.objects.get_or_create(deadline=d, user=user, defaults={"note": request.POST.get("note", "")[:200]})
                qs.filter(key=f"dl:{d.pk}").update(resolved_at=timezone.now(), read_at=timezone.now())
                messages.success(request, f"Marked done: {d.title}")
        nxt = request.POST.get("next", "")
        if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
            return redirect(nxt)
        return redirect("notification_center")

    refresh(user)
    request.session["notif_refreshed_at"] = timezone.now().timestamp()
    cat = request.GET.get("cat", "")
    base = Notification.objects.filter(user=user)
    if cat:
        base = base.filter(category=cat)
    rank = {"urgent": 0, "warn": 1, "info": 2}
    attention = sorted(base.filter(resolved_at__isnull=True, actionable=True),
                       key=lambda n: (rank[n.level], n.due_at or n.updated_at))
    updates = list(base.filter(resolved_at__isnull=True, actionable=False).order_by("-updated_at")[:50])
    history = list(base.filter(resolved_at__isnull=False).order_by("-resolved_at")[:30])
    present = dict(Notification.CATEGORIES)
    categories = [(c, present[c]) for c in Notification.objects.filter(user=user, resolved_at__isnull=True)
                  .order_by().values_list("category", flat=True).distinct()]
    return render(request, "notifications/center.html", {
        "attention": attention, "updates": updates, "history": history, "cat": cat, "categories": categories,
        "my_deadlines": _my_deadlines(user), "counts": open_counts(user),
    })


def _my_deadlines(user):
    """The submission timetable as it applies to this user, with their status on each item."""
    rows = []
    now = timezone.now()
    taught = set()
    if user.is_teacher:
        from django.db.models import Q

        from courses.models import Course
        taught = set(Course.objects.filter(Q(teacher=user) | Q(co_teachers=user)).values_list("pk", flat=True))
    acked = set(DeadlineAck.objects.filter(user=user).values_list("deadline_id", flat=True))
    for d in Deadline.objects.filter(active=True).exclude(kind=Deadline.NOTICE).order_by("due_at"):
        if d.is_auto and d.audience == Deadline.TEACHERS:
            if not taught:
                continue
            per_course = [(c, *course_done(d, c)) for c in deadline_courses(d) if c.pk in taught]
            if per_course:
                rows.append({"d": d, "courses": per_course, "done": all(x[1] for x in per_course)})
        elif deadline_users(d).filter(pk=user.pk).exists():
            rows.append({"d": d, "courses": None, "done": d.pk in acked})
    return [r for r in rows if not r["done"] or (r["d"].due_at and r["d"].due_at > now - timedelta(days=30))][:40]


@login_required
def go(request, notification_id):
    n = get_object_or_404(Notification, pk=notification_id, user=request.user)
    if n.read_at is None:
        n.read_at = timezone.now()
        n.save(update_fields=["read_at"])
    target = n.url if n.url and url_has_allowed_host_and_scheme(n.url, allowed_hosts={request.get_host()}) else "notification_center"
    return redirect(target)


@login_required
def count(request):
    return JsonResponse(open_counts(request.user))


@admin_required
def deadlines(request):
    inst = get_institution()
    form = DeadlineForm(request.POST or None, institution=inst, initial={"remind_days": 7})
    if request.method == "POST" and form.is_valid():
        d = form.save(commit=False)
        d.created_by = request.user
        d.save()
        messages.success(request, f"'{d.title}' added. It now shows in the notification centre of everyone it applies to.")
        return redirect("deadline_detail", d.pk)
    rows = []
    for d in Deadline.objects.select_related("department", "program", "term").order_by("-active", "due_at"):
        rep = deadline_report(d) if d.kind != Deadline.NOTICE else None
        rows.append({"d": d, "rep": rep})
    return render(request, "notifications/deadlines.html", {"form": form, "rows": rows})


@admin_required
def deadline_detail(request, deadline_id):
    inst = get_institution()
    d = get_object_or_404(Deadline, pk=deadline_id)
    form = DeadlineForm(request.POST or None, instance=d, institution=inst)
    if request.method == "POST":
        if request.POST.get("action") == "delete":
            d.delete()
            messages.success(request, "Deadline deleted.")
            return redirect("deadlines")
        if form.is_valid():
            form.save()
            messages.success(request, "Saved.")
            return redirect("deadline_detail", d.pk)
    return render(request, "notifications/deadline_detail.html", {"d": d, "form": form, "rep": deadline_report(d)})
