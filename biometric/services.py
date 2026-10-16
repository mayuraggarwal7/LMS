"""Ingest biometric punches and correlate them with course attendance and teaching.

People are matched by enrolment id: a student's roll number / PRN / admission number, or a
staff member's biometric id / employee id. Presence on a date = first punch to last punch.
"""

import csv
import io
from collections import defaultdict
from datetime import datetime, time, timedelta

from django.utils import timezone

from accounts.models import User
from delivery.models import AttendanceRecord, ClassSession

from .models import Punch

CSV_TIME_FORMATS = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M:%S",
                    "%d/%m/%Y %H:%M", "%Y/%m/%d %H:%M:%S"]


def person_index():
    """biometric id -> user, from student and staff identifiers."""
    index = {}
    for u in User.objects.select_related("student_profile", "faculty_profile").all():
        keys = [u.roll_no, u.prn]
        if hasattr(u, "student_profile"):
            keys.append(u.student_profile.admission_no)
        if hasattr(u, "faculty_profile"):
            keys += [u.faculty_profile.biometric_id, u.faculty_profile.employee_id]
        for k in keys:
            if k:
                index[str(k).strip().lower()] = u
    return index


def parse_time(value):
    value = (value or "").strip()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = None
        for fmt in CSV_TIME_FORMATS:
            try:
                parsed = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        raise ValueError(f"Unrecognised timestamp: {value!r}")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def ingest(rows, device=None, source="api"):
    """rows: iterable of dicts with biometric_id / timestamp / optional direction. Returns a summary."""
    index = person_index()
    created = duplicates = unknown = errors = 0
    unknown_ids = set()
    for row in rows:
        bid = str(row.get("biometric_id") or row.get("user_id") or row.get("id") or "").strip()
        try:
            ts = parse_time(str(row.get("timestamp") or row.get("time") or row.get("datetime") or ""))
        except ValueError:
            errors += 1
            continue
        if not bid:
            errors += 1
            continue
        direction = str(row.get("direction") or row.get("state") or "").strip().lower()
        direction = {"0": "in", "1": "out", "checkin": "in", "checkout": "out", "in": "in", "out": "out"}.get(direction, "auto")
        person = index.get(bid.lower())
        if person is None:
            unknown += 1
            unknown_ids.add(bid)
        _, was_created = Punch.objects.get_or_create(
            biometric_id=bid, timestamp=ts, device=device,
            defaults={"person": person, "direction": direction, "source": source})
        created += int(was_created)
        duplicates += int(not was_created)
    if device is not None:
        device.last_seen = timezone.now()
        device.save(update_fields=["last_seen"])
    return {"created": created, "duplicates": duplicates, "unknown": unknown, "errors": errors,
            "unknown_ids": sorted(unknown_ids)[:20]}


def ingest_csv(text, device=None):
    """CSV export from a device: needs an id column and a timestamp column (header names flexible)."""
    reader = csv.DictReader(io.StringIO(text))
    aliases = {"biometric_id": ["biometric_id", "user_id", "enroll_no", "enrollno", "emp_code", "employee_code", "id", "userid", "roll_no"],
               "timestamp": ["timestamp", "datetime", "punch_time", "time", "log_time", "date_time"],
               "direction": ["direction", "state", "inout", "in_out", "status"]}
    rows = []
    for raw in reader:
        norm = {k.strip().lower().replace(" ", "_"): (v or "").strip() for k, v in raw.items() if k}
        row = {}
        for field, names in aliases.items():
            for name in names:
                if norm.get(name):
                    row[field] = norm[name]
                    break
        if norm.get("date") and norm.get("time") and not any(norm.get(n) for n in ("timestamp", "datetime", "punch_time", "log_time", "date_time")):
            row["timestamp"] = f"{norm['date']} {norm['time']}"  # separate date and time columns
        rows.append(row)
    return ingest(rows, device=device, source="csv")


def presence(users, start, end):
    """{(user_id, date): (first_in, last_out, punches)} for users between two dates (inclusive)."""
    tz = timezone.get_current_timezone()
    lo = timezone.make_aware(datetime.combine(start, time.min), tz)
    hi = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)
    spans = defaultdict(list)
    for p in Punch.objects.filter(person__in=users, timestamp__gte=lo, timestamp__lt=hi).only("person_id", "timestamp"):
        local = timezone.localtime(p.timestamp, tz)
        spans[(p.person_id, local.date())].append(local)
    return {k: (min(v), max(v), len(v)) for k, v in spans.items()}


def _on_campus(span, day, start, end):
    """Was the person on campus during [start, end] of that day? (single punch = present from that time)"""
    if not span:
        return False
    first, last, count = span
    tz = first.tzinfo
    s = datetime.combine(day, start, tzinfo=tz)
    e = datetime.combine(day, end, tzinfo=tz)
    if count == 1:
        return first <= e
    return first <= e and last >= s


def course_correlation(course, start=None, end=None):
    """Compare marked class attendance with biometric presence for a course's delivered sessions.

    Flags: 'present_no_punch' (marked present, no campus presence during the class - proxy risk),
    'absent_on_campus' (on campus during the class but marked absent).
    """
    sessions = list(course.sessions.filter(status=ClassSession.COMPLETED).order_by("date", "start_time"))
    if start:
        sessions = [s for s in sessions if s.date >= start]
    if end:
        sessions = [s for s in sessions if s.date <= end]
    students = list(course.students)
    if not sessions:
        return {"sessions": 0, "rows": [], "flags": [], "agreement": None}
    spans = presence(students, sessions[0].date, sessions[-1].date)
    records = {(r.session_id, r.student_id): r for r in AttendanceRecord.objects.filter(session__in=sessions)}
    flags = []
    per_student = {s.pk: {"student": s, "checked": 0, "agree": 0, "present_no_punch": 0, "absent_on_campus": 0} for s in students}
    for session in sessions:
        for st in students:
            rec = records.get((session.pk, st.pk))
            if rec is None:
                continue
            on_campus = _on_campus(spans.get((st.pk, session.date)), session.date, session.start_time, session.end_time)
            row = per_student[st.pk]
            row["checked"] += 1
            if rec.counts_present and not on_campus:
                row["present_no_punch"] += 1
                flags.append({"kind": "present_no_punch", "session": session, "student": st})
            elif not rec.counts_present and on_campus:
                row["absent_on_campus"] += 1
                flags.append({"kind": "absent_on_campus", "session": session, "student": st})
            else:
                row["agree"] += 1
    checked = sum(r["checked"] for r in per_student.values())
    agree = sum(r["agree"] for r in per_student.values())
    return {"sessions": len(sessions), "rows": list(per_student.values()), "flags": flags,
            "agreement": round(agree / checked * 100, 1) if checked else None}


def staff_attendance(staff_users, start, end):
    """Per staff member: days present, hours on campus, classes taught while not punched in."""
    spans = presence(staff_users, start, end)
    result = []
    for u in staff_users:
        days = sorted(d for (uid, d) in spans if uid == u.pk)
        hours = sum((spans[(u.pk, d)][1] - spans[(u.pk, d)][0]).total_seconds() / 3600 for d in days)
        taught = ClassSession.objects.filter(status=ClassSession.COMPLETED, date__gte=start, date__lte=end,
                                             course__teacher=u)
        unpunched = [s for s in taught if not _on_campus(spans.get((u.pk, s.date)), s.date, s.start_time, s.end_time)]
        result.append({"user": u, "days_present": len(days), "hours": round(hours, 1), "classes_taught": taught.count(),
                       "classes_without_punch": unpunched})
    return result


def days_present(user, start, end):
    return len({d for (uid, d) in presence([user], start, end) if uid == user.pk})
