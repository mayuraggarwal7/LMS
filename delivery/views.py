import csv

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from courses.access import course_member, course_teacher, teacher_required
from courses.models import Course
from courses.terms import current_term, in_term
from courses.forms import DATE

from .models import WEEKDAYS, AttendanceRecord, ClassSession, Holiday, SupplementaryDay, TimetableSlot
from .services import (add_supplementary_day, attendance_stats, build_ics, capacity, carry_over, declare_holiday,
                       plan_progress, reschedule_session, sync_timetable)

TIME = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")


class SlotForm(forms.ModelForm):
    class Meta:
        model = TimetableSlot
        fields = ["weekday", "start_time", "end_time", "session_type", "room", "batch"]
        widgets = {"start_time": TIME, "end_time": TIME}

    def clean(self):
        data = super().clean()
        if data.get("start_time") and data.get("end_time") and data["end_time"] <= data["start_time"]:
            raise forms.ValidationError("End time must be after start time.")
        return data


class HolidayForm(forms.ModelForm):
    class Meta:
        model = Holiday
        fields = ["date", "name"]
        widgets = {"date": DATE}


class SupplementaryForm(forms.ModelForm):
    class Meta:
        model = SupplementaryDay
        fields = ["date", "follows_weekday", "reason"]
        widgets = {"date": DATE}


class RescheduleForm(forms.Form):
    date = forms.DateField(widget=DATE)
    start_time = forms.TimeField(widget=TIME)
    end_time = forms.TimeField(widget=TIME)
    room = forms.CharField(required=False)

    def clean(self):
        data = super().clean()
        if data.get("start_time") and data.get("end_time") and data["end_time"] <= data["start_time"]:
            raise forms.ValidationError("End time must be after start time.")
        return data


class DatesForm(forms.Form):
    start_date = forms.DateField(widget=DATE)
    end_date = forms.DateField(widget=DATE)


class SessionForm(forms.ModelForm):
    class Meta:
        model = ClassSession
        fields = ["coverage", "actual_topics", "notes", "notes_file", "resource_link", "notes_shared"]
        widgets = {"actual_topics": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 10})}


class ExtraSessionForm(forms.ModelForm):
    class Meta:
        model = ClassSession
        fields = ["date", "start_time", "end_time", "session_type", "room", "plan_item"]
        widgets = {"date": DATE, "start_time": TIME, "end_time": TIME}


@course_teacher
def timetable(request, course):
    slot_form = SlotForm(prefix="slot")
    holiday_form = HolidayForm(prefix="hol")
    supp_form = SupplementaryForm(prefix="supp")
    dates_form = DatesForm(initial={"start_date": course.start_date, "end_date": course.end_date}, prefix="dates")
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "slot":
            slot_form = SlotForm(request.POST, prefix="slot")
            if slot_form.is_valid():
                slot = slot_form.save(commit=False)
                slot.course = course
                slot.save()
                messages.success(request, f"Added {slot}.")
                return redirect("timetable", course.pk)
        elif action == "holiday":
            holiday_form = HolidayForm(request.POST, prefix="hol")
            if holiday_form.is_valid():
                result = declare_holiday([course], holiday_form.cleaned_data["date"], holiday_form.cleaned_data["name"])[course]
                messages.success(request, "Holiday added; classes that day are cancelled and the plan shifted forward.")
                _warn_capacity(request, course, result)
                return redirect("timetable", course.pk)
        elif action == "supplementary":
            supp_form = SupplementaryForm(request.POST, prefix="supp")
            if supp_form.is_valid():
                cd = supp_form.cleaned_data
                add_supplementary_day([course], cd["date"], cd["follows_weekday"], cd["reason"])
                messages.success(request, f"Supplementary teaching day added on {cd['date']:%a %d %b}.")
                return redirect("timetable", course.pk)
        elif action == "delete_supplementary":
            SupplementaryDay.objects.filter(course=course, pk=request.POST.get("id")).delete()
            sync_timetable(course)
            return redirect("timetable", course.pk)
        elif action == "dates":
            dates_form = DatesForm(request.POST, prefix="dates")
            if dates_form.is_valid():
                course.start_date = dates_form.cleaned_data["start_date"]
                course.end_date = dates_form.cleaned_data["end_date"]
                course.save(update_fields=["start_date", "end_date"])
                messages.success(request, "Semester dates saved.")
                return redirect("timetable", course.pk)
        elif action == "sync":
            result = sync_timetable(course)
            if result["errors"]:
                for e in result["errors"]:
                    messages.error(request, e)
            else:
                msg = (f"Synced: {result['created']} new sessions, {result['mapped']} plan items scheduled, "
                       f"{result['buffer']} buffer/revision slots.")
                if result["removed"]:
                    msg += f" {result['removed']} obsolete sessions removed."
                messages.success(request, msg)
                if result["unscheduled"]:
                    messages.warning(request, f"{result['unscheduled']} plan items do not fit in the available slots - "
                                              "add slots, extend the dates or merge plan items.")
            return redirect("timetable", course.pk)
        elif action == "delete_slot":
            TimetableSlot.objects.filter(course=course, pk=request.POST.get("id")).delete()
            messages.success(request, "Slot removed. Sync to update the calendar.")
            return redirect("timetable", course.pk)
        elif action == "delete_holiday":
            holiday = Holiday.objects.filter(course=course, pk=request.POST.get("id")).first()
            if holiday:
                course.sessions.filter(date=holiday.date, status=ClassSession.CANCELLED,
                                       cancel_reason__startswith="Holiday").update(status=ClassSession.SCHEDULED, cancel_reason="")
                holiday.delete()
                sync_timetable(course)
            return redirect("timetable", course.pk)
    slots = course.slots.all()
    weekly = {
        "lecture": sum(1 for s in slots if s.session_type == "lecture"),
        "tutorial": sum(1 for s in slots if s.session_type == "tutorial"),
        "lab": sum(1 for s in slots if s.session_type == "lab"),
    }
    plan_counts = {t: course.plan_items.filter(session_type=t).count() for t in ("lecture", "tutorial", "lab")}
    return render(request, "delivery/timetable.html", {
        "course": course, "tab": "timetable", "slots": slots, "holidays": course.holidays.all(),
        "slot_form": slot_form, "holiday_form": holiday_form, "dates_form": dates_form, "supp_form": supp_form,
        "supplementary": course.supplementary_days.all(), "capacity": capacity(course),
        "weekly": weekly, "plan_counts": plan_counts, "session_count": course.sessions.count(),
    })


@course_member
def calendar_ics(request, course):
    response = HttpResponse(build_ics(course), content_type="text/calendar")
    response["Content-Disposition"] = f'attachment; filename="{course.code}-schedule.ics"'
    return response


@course_member
def session_list(request, course):
    sessions = course.sessions.select_related("plan_item__unit").prefetch_related("attendance")
    show = request.GET.get("show", "all")
    today = timezone.localdate()
    if show == "upcoming":
        sessions = sessions.filter(date__gte=today)
    elif show == "past":
        sessions = sessions.filter(date__lt=today)
    elif show == "pending":
        sessions = sessions.filter(date__lte=today, status=ClassSession.SCHEDULED)
    if not request.is_course_teacher:
        sessions = sessions.exclude(status=ClassSession.CANCELLED)
    my_att = {}
    if not request.is_course_teacher:
        my_att = dict(AttendanceRecord.objects.filter(session__course=course, student=request.user).values_list("session_id", "status"))
    return render(request, "delivery/sessions.html", {
        "course": course, "tab": "sessions", "sessions": sessions, "show": show, "today": today,
        "progress": plan_progress(course), "my_att": my_att,
    })


@course_member
def session_detail(request, course, session_id):
    session = get_object_or_404(ClassSession.objects.select_related("plan_item__unit"), pk=session_id, course=course)
    if not request.is_course_teacher:
        record = session.attendance.filter(student=request.user).first()
        return render(request, "delivery/session_student.html", {"course": course, "tab": "sessions", "session": session, "record": record})

    form = SessionForm(request.POST or None, request.FILES or None, instance=session)
    students = list(course.students)
    if request.method == "POST":
        action = request.POST.get("action", "save")
        if action == "cancel":
            session.status = ClassSession.CANCELLED
            session.cancel_reason = (request.POST.get("reason") or "Not held")[:160]
            session.save(update_fields=["status", "cancel_reason"])
            sync_timetable(course)
            messages.success(request, "Session cancelled; the plan shifted to the next available slot.")
            return redirect("session_detail", course.pk, session.pk)
        if action == "reschedule":
            rform = RescheduleForm(request.POST, prefix="rs")
            if rform.is_valid():
                cd = rform.cleaned_data
                new = reschedule_session(session, cd["date"], cd["start_time"], cd["end_time"], cd["room"])
                messages.success(request, f"Class moved to {new.date:%a %d %b} {new.start_time:%H:%M}.")
                return redirect("session_detail", course.pk, new.pk)
            messages.error(request, "Enter a valid new date and time.")
            return redirect("session_detail", course.pk, session.pk)
        if action == "reopen":
            session.status = ClassSession.SCHEDULED
            session.cancel_reason = ""
            session.save(update_fields=["status", "cancel_reason"])
            sync_timetable(course)
            return redirect("session_detail", course.pk, session.pk)
        if action == "checkin":
            session.checkin_open = not session.checkin_open
            session.save(update_fields=["checkin_open"])
            return redirect("session_detail", course.pk, session.pk)
        if form.is_valid():
            session = form.save(commit=False)
            # attendance
            for student in students:
                status = request.POST.get(f"att-{student.pk}")
                if status in dict(AttendanceRecord.STATUS_CHOICES):
                    AttendanceRecord.objects.update_or_create(session=session, student=student, defaults={"status": status})
            if action == "complete":
                session.status = ClassSession.COMPLETED
                session.delivered_at = timezone.now()
                session.checkin_open = False
                if not session.coverage:
                    session.coverage = "full"
            session.save()
            if action == "complete" and session.coverage == "partial" and request.POST.get("carry_over"):
                carry_over(session)
                messages.info(request, "Remaining content carried over to the next session; plan re-synced.")
            messages.success(request, "Session saved." if action != "complete" else "Session marked as delivered.")
            return redirect("session_detail", course.pk, session.pk)
    records = {r.student_id: r for r in session.attendance.all()}
    roster = [{"student": s, "record": records.get(s.pk)} for s in students]
    present = sum(1 for r in records.values() if r.counts_present)
    return render(request, "delivery/session_detail.html", {
        "course": course, "tab": "sessions", "session": session, "form": form, "roster": roster,
        "present": present, "has_records": bool(records),
        "reschedule_form": RescheduleForm(prefix="rs", initial={"start_time": session.start_time, "end_time": session.end_time, "room": session.room}),
        "prev": course.sessions.filter(date__lt=session.date).order_by("-date", "-start_time").first(),
        "next": course.sessions.filter(date__gt=session.date).order_by("date", "start_time").first(),
    })


@course_teacher
def session_add(request, course):
    form = ExtraSessionForm(request.POST or None)
    form.fields["plan_item"].queryset = course.plan_items.all()
    form.fields["plan_item"].required = False
    if request.method == "POST" and form.is_valid():
        session = form.save(commit=False)
        session.course = course
        session.is_extra = True
        session.plan_pinned = bool(session.plan_item)
        session.save()
        sync_timetable(course)
        messages.success(request, "Extra session added.")
        return redirect("session_detail", course.pk, session.pk)
    return render(request, "delivery/session_add.html", {"course": course, "tab": "sessions", "form": form})


@login_required
@require_POST
def student_checkin(request, course_id):
    course = get_object_or_404(Course, pk=course_id, enrollments__student=request.user)
    code = (request.POST.get("code") or "").strip()
    session = course.sessions.filter(checkin_open=True, checkin_code=code, date=timezone.localdate()).first()
    if not session:
        messages.error(request, "Check-in code not valid or check-in is closed.")
    else:
        AttendanceRecord.objects.update_or_create(
            session=session, student=request.user, defaults={"status": AttendanceRecord.PRESENT, "self_checked_in": True}
        )
        messages.success(request, "You're marked present. ✔")
    return redirect("course_detail", course.pk)


@course_member
def attendance_report(request, course):
    sessions = list(course.sessions.filter(status=ClassSession.COMPLETED).order_by("date", "start_time"))
    students = list(course.students) if request.is_course_teacher else [request.user]
    records = {}
    for r in AttendanceRecord.objects.filter(session__in=sessions, student__in=students):
        records[(r.student_id, r.session_id)] = r.status
    stats = attendance_stats(course, students)
    rows = [{"student": s, "cells": [records.get((s.pk, sess.pk), "") for sess in sessions], "stats": stats[s.pk]} for s in students]
    if request.GET.get("format") == "csv" and request.is_course_teacher:
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{course.code}-attendance.csv"'
        writer = csv.writer(response)
        writer.writerow(["Roll no", "Name"] + [f"{s.date} {s.start_time:%H:%M}" for s in sessions] + ["Present", "Total", "%"])
        for row in rows:
            writer.writerow([row["student"].roll_no, row["student"].display_name] + row["cells"]
                            + [row["stats"]["present"], row["stats"]["total"], row["stats"]["percent"]])
        return response
    return render(request, "delivery/attendance_report.html", {
        "course": course, "tab": "attendance", "sessions": sessions, "rows": rows,
    })


def _teacher_courses(request):
    user = request.user
    qs = Course.objects.filter(teacher=user) | Course.objects.filter(co_teachers=user)
    if user.is_superuser:
        qs = Course.objects.all()
    return in_term(qs.distinct(), current_term(request)).filter(archived=False)


@teacher_required
def pending_attendance(request):
    """All classes (today and earlier) whose attendance hasn't been marked, across the teacher's courses."""
    courses = list(_teacher_courses(request))
    today = timezone.localdate()
    if request.method == "POST":
        ids = request.POST.getlist("selected") or [request.POST.get("session")]
        sessions = ClassSession.objects.filter(pk__in=[i for i in ids if i], course__in=courses, status=ClassSession.SCHEDULED)
        touched = set()
        removed = 0
        for session in sessions:
            touched.add(session.course)
            if session.is_extra:
                session.delete()  # manually added: really remove it
            else:
                session.status = ClassSession.CANCELLED  # timetable class that did not happen
                session.save(update_fields=["status"])
            removed += 1
        for course in touched:
            sync_timetable(course)
        messages.success(request, f"Deleted {removed} class{'es' if removed != 1 else ''}; the learning plan moved to the next available slots.")
        return redirect("pending_attendance")
    pending = (ClassSession.objects.filter(course__in=courses, date__lte=today, status=ClassSession.SCHEDULED)
               .filter(attendance__isnull=True).select_related("course", "plan_item").order_by("date", "start_time").distinct())
    course_filter = request.GET.get("course")
    summary = []
    for c in courses:
        marked = c.sessions.filter(status=ClassSession.COMPLETED).count()
        waiting = sum(1 for p in pending if p.course_id == c.pk)
        summary.append({"course": c, "marked": marked, "pending": waiting})
    if course_filter:
        pending = [p for p in pending if str(p.course_id) == course_filter]
    return render(request, "delivery/pending_attendance.html", {
        "summary": summary, "pending": pending, "course_filter": course_filter,
        "total_marked": sum(r["marked"] for r in summary), "total_pending": sum(r["pending"] for r in summary),
    })


def _warn_capacity(request, course, result):
    cap = capacity(course)
    if cap["shortfall"]:
        messages.warning(request, f"{course.code}: {cap['shortfall']} plan session(s) no longer fit before the end date - "
                                  "add supplementary teaching days or extra classes.")


class ScheduleChangeForm(forms.Form):
    KIND = [("holiday", "Sudden holiday / no classes"), ("supplementary", "Supplementary teaching day")]
    kind = forms.ChoiceField(choices=KIND, widget=forms.RadioSelect, initial="holiday")
    date = forms.DateField(widget=DATE)
    name = forms.CharField(required=False, label="Reason", help_text="e.g. Heavy rain, Institute event, compensation for 15 Aug")
    follows_weekday = forms.TypedChoiceField(choices=WEEKDAYS, coerce=int, required=False,
                                             label="Supplementary day follows the timetable of")
    courses = forms.ModelMultipleChoiceField(queryset=Course.objects.none(), widget=forms.CheckboxSelectMultiple,
                                             required=False, help_text="Leave all ticked to apply to every class you teach")
    whole_institution = forms.BooleanField(required=False, label="Apply to all courses in this term (administrators)")


@teacher_required
def schedule_changes(request):
    """Declare a sudden holiday or a supplementary teaching day across classes in one go."""
    courses = _teacher_courses(request)
    form = ScheduleChangeForm(request.POST or None, initial={"courses": courses})
    form.fields["courses"].queryset = courses
    if not request.user.is_staff:
        del form.fields["whole_institution"]
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        targets = list(cd["courses"]) or list(courses)
        if cd.get("whole_institution"):
            targets = list(in_term(Course.objects.filter(archived=False), current_term(request)))
        if cd["kind"] == "holiday":
            results = declare_holiday(targets, cd["date"], cd["name"] or "Holiday")
            messages.success(request, f"{cd['date']:%a %d %b} declared a holiday for {len(targets)} course(s); plans shifted forward.")
            for course, result in results.items():
                _warn_capacity(request, course, result)
        else:
            if cd.get("follows_weekday") is None:
                form.add_error("follows_weekday", "Pick which weekday's timetable to follow.")
                return render(request, "delivery/schedule_changes.html", _schedule_ctx(request, form, courses))
            add_supplementary_day(targets, cd["date"], cd["follows_weekday"], cd["name"])
            messages.success(request, f"Supplementary teaching day on {cd['date']:%a %d %b} added for {len(targets)} course(s).")
        return redirect("schedule_changes")
    return render(request, "delivery/schedule_changes.html", _schedule_ctx(request, form, courses))


def _schedule_ctx(request, form, courses):
    rows = []
    for c in courses:
        rows.append({"course": c, "capacity": capacity(c), "holidays": list(c.holidays.all()),
                     "supplementary": list(c.supplementary_days.all())})
    cancelled = (ClassSession.objects.filter(course__in=courses, status=ClassSession.CANCELLED)
                 .select_related("course").order_by("-date")[:30])
    return {"form": form, "rows": rows, "cancelled": cancelled}
