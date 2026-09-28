import csv

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from courses.access import course_member, course_teacher
from courses.forms import DATE

from .models import AttendanceRecord, ClassSession, Holiday, TimetableSlot
from .services import attendance_stats, build_ics, carry_over, plan_progress, sync_timetable

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
                Holiday.objects.update_or_create(course=course, date=holiday_form.cleaned_data["date"],
                                                 defaults={"name": holiday_form.cleaned_data["name"]})
                messages.success(request, "Holiday added. Sync to reschedule.")
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
            Holiday.objects.filter(course=course, pk=request.POST.get("id")).delete()
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
        "slot_form": slot_form, "holiday_form": holiday_form, "dates_form": dates_form,
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
            session.save(update_fields=["status"])
            sync_timetable(course)
            messages.success(request, "Session cancelled; the plan shifted to the next available slot.")
            return redirect("session_detail", course.pk, session.pk)
        if action == "reopen":
            session.status = ClassSession.SCHEDULED
            session.save(update_fields=["status"])
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
        session.save()
        messages.success(request, "Extra session added.")
        return redirect("session_detail", course.pk, session.pk)
    return render(request, "delivery/session_add.html", {"course": course, "tab": "sessions", "form": form})


@login_required
@require_POST
def student_checkin(request, course_id):
    from courses.models import Course

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
