"""Timetable <-> learning plan synchronisation, attendance statistics, ICS export."""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from courses.models import PlanItem

from .models import AttendanceRecord, ClassSession


def occurrences(course):
    """All (date, slot) pairs for the term: weekly slots between the start and end dates, skipping
    holidays, plus supplementary days that run another weekday's timetable."""
    if not course.start_date or not course.end_date:
        return []
    holidays = set(course.holidays.values_list("date", flat=True))
    slots = list(course.slots.all())
    result = []
    day = course.start_date
    while day <= course.end_date:
        if day not in holidays:
            for slot in slots:
                if slot.weekday == day.weekday():
                    result.append((day, slot))
        day += timedelta(days=1)
    for extra in course.supplementary_days.all():
        if extra.date in holidays:
            continue
        for slot in slots:
            if slot.weekday == extra.follows_weekday:
                result.append((extra.date, slot))
    result.sort(key=lambda pair: (pair[0], pair[1].start_time))
    return result


@transaction.atomic
def sync_timetable(course):
    """Generate dated class sessions from the timetable and map plan items onto them.

    * Delivered, cancelled, manually-added sessions and sessions that already
      have notes or attendance are never deleted or re-mapped.
    * Plan items already delivered are not scheduled again.
    * Remaining plan items are laid out in sequence onto the upcoming sessions of
      the matching type (tutorial items fall back to lecture slots when no
      tutorial slots exist; same for labs).
    Returns a summary dict.
    """
    summary = {"created": 0, "removed": 0, "mapped": 0, "unscheduled": 0, "buffer": 0, "errors": []}
    if not course.start_date or not course.end_date:
        summary["errors"].append("Set the semester start and end dates first.")
        return summary
    if not course.slots.exists():
        summary["errors"].append("Add at least one weekly timetable slot first.")
        return summary

    wanted = {(d, s.start_time): s for d, s in occurrences(course)}
    existing = {}
    for session in course.sessions.all():
        key = (session.date, session.start_time)
        if key in wanted and key not in existing:
            existing[key] = session
            if session.slot_id != wanted[key].pk and not session.is_locked:
                session.slot = wanted[key]
                session.session_type = wanted[key].session_type
                session.end_time = wanted[key].end_time
                session.room = wanted[key].room
                session.save()
        elif not session.is_locked:
            session.delete()
            summary["removed"] += 1

    for key, slot in wanted.items():
        if key not in existing:
            existing[key] = ClassSession.objects.create(
                course=course, slot=slot, date=key[0], start_time=slot.start_time, end_time=slot.end_time,
                session_type=slot.session_type, room=slot.room,
            )
            summary["created"] += 1

    # map plan items onto open sessions
    delivered = set(
        course.sessions.filter(status=ClassSession.COMPLETED, plan_item__isnull=False).values_list("plan_item_id", flat=True)
    )
    open_sessions = list(course.sessions.filter(status=ClassSession.SCHEDULED).order_by("date", "start_time"))
    # sessions with notes/attendance or a hand-picked plan item keep their mapping
    locked_items = {s.plan_item_id for s in open_sessions if s.keeps_plan_item and s.plan_item_id}
    queue_by_type = {}
    for item in course.plan_items.order_by("sequence"):
        if item.pk in delivered or item.pk in locked_items:
            continue
        queue_by_type.setdefault(item.session_type, []).append(item)

    slot_types = set(course.slots.values_list("session_type", flat=True))
    for t in ("tutorial", "lab"):
        if t in queue_by_type and t not in slot_types:
            queue_by_type.setdefault("lecture", []).extend(queue_by_type.pop(t))
            queue_by_type["lecture"].sort(key=lambda i: i.sequence)

    for session in open_sessions:
        if session.keeps_plan_item and session.plan_item_id:
            continue
        queue = queue_by_type.get(session.session_type) or []
        item = queue.pop(0) if queue else None
        if session.plan_item_id != (item.pk if item else None):
            session.plan_item = item
            session.save(update_fields=["plan_item"])
        if item:
            summary["mapped"] += 1
        else:
            summary["buffer"] += 1
    summary["unscheduled"] = sum(len(q) for q in queue_by_type.values())
    return summary


@transaction.atomic
def carry_over(session):
    """Partially covered session: insert a continuation plan item right after it and re-sync."""
    item = session.plan_item
    if not item:
        return None
    course = session.course
    PlanItem.objects.filter(course=course, sequence__gt=item.sequence).update(sequence=F("sequence") + 1)
    new = PlanItem.objects.create(
        course=course, sequence=item.sequence + 1, session_type=item.session_type, unit=item.unit,
        title=f"(contd.) {item.title}"[:400], objective=item.objective, bloom_level=item.bloom_level,
        duration_minutes=item.duration_minutes, pedagogy=item.pedagogy, tools=item.tools,
        in_class="Recap of the previous session, then complete the remaining content.",
        post_class=item.post_class,
    )
    new.topics.set(item.topics.all())
    new.outcomes.set(item.outcomes.all())
    sync_timetable(course)
    return new


@transaction.atomic
def declare_holiday(courses, day, name, reason_prefix="Holiday"):
    """Sudden holiday: cancel that day's scheduled classes in the given courses and shift their plans.

    Classes already delivered (or with attendance) are left alone. Returns per-course sync summaries.
    """
    from .models import Holiday

    results = {}
    for course in courses:
        Holiday.objects.update_or_create(course=course, date=day, defaults={"name": name})
        for session in course.sessions.filter(date=day, status=ClassSession.SCHEDULED):
            if session.attendance.exists():
                continue
            session.status = ClassSession.CANCELLED
            session.cancel_reason = f"{reason_prefix}: {name}" if name else reason_prefix
            session.plan_pinned = False
            session.save(update_fields=["status", "cancel_reason", "plan_pinned"])
        results[course] = sync_timetable(course)
    return results


@transaction.atomic
def add_supplementary_day(courses, day, follows_weekday, reason=""):
    """Extra teaching day running another weekday's timetable; the plan pulls forward onto it."""
    from .models import Holiday, SupplementaryDay

    results = {}
    for course in courses:
        Holiday.objects.filter(course=course, date=day).delete()
        SupplementaryDay.objects.update_or_create(
            course=course, date=day, defaults={"follows_weekday": follows_weekday, "reason": reason})
        if course.end_date and day > course.end_date:
            pass  # compensation after the last teaching day still counts; occurrences() includes it
        results[course] = sync_timetable(course)
    return results


@transaction.atomic
def reschedule_session(session, new_date, start_time, end_time, room=""):
    """Move one class: the original is kept as cancelled ('rescheduled to ...'), a new class takes its plan item."""
    new = ClassSession.objects.create(
        course=session.course, date=new_date, start_time=start_time, end_time=end_time,
        session_type=session.session_type, room=room or session.room, is_extra=True,
        plan_item=session.plan_item, plan_pinned=bool(session.plan_item),
    )
    session.status = ClassSession.CANCELLED
    session.cancel_reason = f"Rescheduled to {new_date:%d %b %Y} {start_time:%H:%M}"
    session.plan_pinned = False
    session.save(update_fields=["status", "cancel_reason", "plan_pinned"])
    sync_timetable(session.course)
    return new


def capacity(course):
    """Teaching capacity vs plan: how many sessions remain vs plan items still to deliver."""
    delivered = set(course.sessions.filter(status=ClassSession.COMPLETED, plan_item__isnull=False)
                    .values_list("plan_item_id", flat=True))
    remaining_items = course.plan_items.exclude(pk__in=delivered).count()
    open_sessions = course.sessions.filter(status=ClassSession.SCHEDULED).count()
    return {"remaining_items": remaining_items, "open_sessions": open_sessions,
            "shortfall": max(0, remaining_items - open_sessions)}


def plan_progress(course):
    total = course.plan_items.count()
    delivered = course.sessions.filter(status=ClassSession.COMPLETED, plan_item__isnull=False).values("plan_item").distinct().count()
    today = timezone.localdate()
    due = course.sessions.filter(date__lt=today, plan_item__isnull=False).exclude(status=ClassSession.CANCELLED).count()
    return {
        "total": total, "delivered": delivered, "percent": round(delivered / total * 100) if total else 0,
        "expected_by_now": due, "behind": max(0, due - delivered),
    }


def attendance_stats(course, students=None):
    """{student_id: {"present": n, "total": n, "percent": float}} over delivered sessions."""
    students = list(students if students is not None else course.students)
    sessions = list(course.sessions.filter(status=ClassSession.COMPLETED).values_list("pk", flat=True))
    records = AttendanceRecord.objects.filter(session_id__in=sessions)
    counts = {s.pk: {"present": 0, "total": len(sessions)} for s in students}
    for r in records:
        if r.student_id in counts and r.counts_present:
            counts[r.student_id]["present"] += 1
    for c in counts.values():
        c["percent"] = round(c["present"] / c["total"] * 100, 1) if c["total"] else None
        c["at_risk"] = c["percent"] is not None and c["percent"] < settings.LMS_ATTENDANCE_THRESHOLD
    return counts


def build_ics(course):
    """iCalendar feed of all sessions (import into Google Calendar / Outlook)."""
    tz = timezone.get_current_timezone()

    def fmt(d, t):
        return datetime.combine(d, t, tzinfo=tz).astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//CampusFlow LMS//EN", f"X-WR-CALNAME:{course.code} {course.title}"]
    stamp = timezone.now().astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for s in course.sessions.select_related("plan_item").exclude(status=ClassSession.CANCELLED):
        summary = f"{course.code} {s.get_session_type_display()}"
        if s.plan_item:
            summary += f": {s.plan_item.title[:60]}"
        desc = (s.plan_item.objective if s.plan_item else "").replace("\n", " ")
        lines += [
            "BEGIN:VEVENT", f"UID:session-{s.pk}@campusflow", f"DTSTAMP:{stamp}",
            f"DTSTART:{fmt(s.date, s.start_time)}", f"DTEND:{fmt(s.date, s.end_time)}",
            f"SUMMARY:{_ics_escape(summary)}", f"DESCRIPTION:{_ics_escape(desc)}", f"LOCATION:{_ics_escape(s.room)}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _ics_escape(text):
    return (text or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")
