"""Work out what needs each user's attention and keep their notification centre in step with it.

Items come from two places:
  * live LMS data - classes without attendance, submissions waiting for a grade, exams without marks,
    assignments due, low attendance, fee dues;
  * deadlines the administration sets for groups of users (paper submission, marks entry, ...).
An item disappears from "Needs attention" by itself once the underlying work is done.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from classroom.models import Assignment, Submission
from courses.models import AcademicTerm, Course
from courses.terms import in_term
from delivery.models import ClassSession
from delivery.services import attendance_stats
from exams.models import Exam

from .models import Deadline, DeadlineAck, Notification

REFRESH_SECONDS = 300
ATTENDANCE_GRACE_DAYS = 2   # attendance not marked for longer than this is overdue
GRADING_GRACE_DAYS = 7      # submissions waiting longer than this are overdue
MARKS_GRACE_DAYS = 7        # exam marks not released this long after the exam are overdue


def _active_term():
    return AcademicTerm.objects.filter(is_active=True).first()


def _end_of_day(day):
    return timezone.make_aware(datetime.combine(day, time(23, 59)), timezone.get_current_timezone())


def _when(dt):
    return timezone.localtime(dt).strftime("%d %b %Y, %H:%M") if dt else ""


def _level_for_due(due_at, remind_days):
    """urgent when past due, warn inside the reminder window, info before that."""
    if due_at is None:
        return Notification.INFO
    now = timezone.now()
    if now > due_at:
        return Notification.URGENT
    if now >= due_at - timedelta(days=remind_days):
        return Notification.WARN
    return Notification.INFO


# ---------------------------------------------------------------- deadlines: who and whether done

def deadline_courses(deadline):
    """Courses a teacher deadline applies to."""
    qs = Course.objects.filter(archived=False)
    qs = in_term(qs, deadline.term or _active_term())
    if deadline.program_id:
        qs = qs.filter(program_id=deadline.program_id)
    if deadline.department_id:
        dept = deadline.department
        qs = qs.filter(Q(program__department=dept) | Q(department__iexact=dept.name))
    if deadline.year:
        qs = qs.filter(year=deadline.year)
    return qs.select_related("teacher").prefetch_related("co_teachers").distinct()


def course_teachers(course):
    return [course.teacher, *course.co_teachers.all()]


def _matching_exams(course, deadline):
    qs = course.exams.filter(purpose=Exam.REGULAR)
    if deadline.component_kind:
        qs = qs.filter(component__kind=deadline.component_kind)
    return qs


def course_done(deadline, course):
    """(done, detail) for an automatically checked deadline on one course."""
    kind = deadline.kind
    if kind == Deadline.PAPER:
        exams = list(_matching_exams(course, deadline).prefetch_related("paper"))
        if not exams:
            return False, "No exam created yet"
        submitted = [e for e in exams if e.status in (Exam.PUBLISHED, Exam.MARKED) and e.paper.exists()]
        if submitted:
            return True, f"Submitted: {submitted[0].title}"
        if not any(e.paper.exists() for e in exams):
            return False, "Exam created, paper has no questions"
        return False, "Paper still in draft (set the exam to Scheduled to submit)"
    if kind == Deadline.MARKS:
        exams = list(_matching_exams(course, deadline))
        if not exams:
            return False, "No exam created yet"
        pending = [e for e in exams if e.status != Exam.MARKED]
        return (not pending), ("All marks released" if not pending else f"Marks not released: {pending[0].title}")
    if kind == Deadline.ATTENDANCE:
        upto = min(timezone.localdate(), timezone.localdate(deadline.due_at)) if deadline.due_at else timezone.localdate()
        n = (course.sessions.filter(date__lte=upto, status=ClassSession.SCHEDULED, attendance__isnull=True)
             .distinct().count())
        return n == 0, ("Up to date" if n == 0 else f"{n} class{'es' if n != 1 else ''} without attendance")
    if kind == Deadline.PLAN:
        has_plan = course.plan_items.exists()
        has_sessions = course.sessions.exists()
        if has_plan and has_sessions:
            return True, "Plan and timetable in place"
        return False, "No learning plan yet" if not has_plan else "Timetable not added / synced"
    if kind == Deadline.GRADING:
        subs = Submission.objects.filter(assignment__course=course, status=Submission.TURNED_IN)
        if deadline.due_at:
            subs = subs.filter(Q(assignment__due_at__lte=deadline.due_at) | Q(assignment__due_at__isnull=True))
        n = subs.count()
        return n == 0, ("All graded" if n == 0 else f"{n} submission{'s' if n != 1 else ''} to grade")
    return False, ""


def deadline_users(deadline):
    """Users a user-level (custom / notice) deadline applies to."""
    aud = deadline.audience
    users = User.objects.none()
    if aud in (Deadline.TEACHERS,):
        ids = set()
        for c in deadline_courses(deadline):
            ids.update(u.pk for u in course_teachers(c))
        users = User.objects.filter(pk__in=ids)
    elif aud == Deadline.ALL_STAFF:
        users = User.objects.filter(Q(role=User.TEACHER) | Q(faculty_profile__isnull=False) | Q(is_staff=True))
        if deadline.department_id:
            users = users.filter(Q(faculty_profile__department=deadline.department) | Q(department__iexact=deadline.department.name))
    elif aud in (Deadline.STUDENTS, Deadline.EVERYONE):
        students = User.objects.filter(role=User.STUDENT)
        if deadline.program_id or deadline.department_id or deadline.year:
            q = Q()
            prof = Q(student_profile__isnull=False)
            if deadline.program_id:
                prof &= Q(student_profile__program_id=deadline.program_id)
            if deadline.department_id:
                prof &= Q(student_profile__program__department=deadline.department)
            if deadline.year:
                prof &= Q(student_profile__current_semester__in=[2 * deadline.year - 1, 2 * deadline.year])
            q |= prof
            q |= Q(enrollments__course__in=deadline_courses(deadline))
            students = students.filter(q)
        users = students
        if aud == Deadline.EVERYONE:
            users = User.objects.filter(Q(pk__in=students.values("pk")) | Q(role=User.TEACHER) | Q(is_staff=True))
    return users.filter(is_active=True).distinct()


def deadline_report(deadline):
    """Rows for the admin compliance view."""
    if deadline.is_auto and deadline.audience == Deadline.TEACHERS:
        rows = []
        for c in deadline_courses(deadline):
            done, detail = course_done(deadline, c)
            rows.append({"course": c, "people": course_teachers(c), "done": done, "detail": detail})
        rows.sort(key=lambda r: (r["done"], r["course"].code))
        return {"by_course": True, "rows": rows, "done": sum(r["done"] for r in rows), "total": len(rows)}
    acked = dict(DeadlineAck.objects.filter(deadline=deadline).values_list("user_id", "done_at"))
    rows = [{"user": u, "done": u.pk in acked, "done_at": acked.get(u.pk)} for u in deadline_users(deadline)[:2000]]
    rows.sort(key=lambda r: (r["done"], r["user"].display_name))
    return {"by_course": False, "rows": rows, "done": sum(r["done"] for r in rows), "total": len(rows)}


def _deadlines_for(user):
    return Deadline.objects.filter(active=True).select_related("department", "program", "term")


# ---------------------------------------------------------------- collecting items per user

def _teacher_items(user, term):
    items = []
    courses = list(in_term(Course.objects.filter(Q(teacher=user) | Q(co_teachers=user), archived=False), term).distinct())
    if not courses:
        return items, courses
    today = timezone.localdate()
    pending_url = reverse("pending_attendance")
    for c in courses:
        pend = list(c.sessions.filter(date__lte=today, status=ClassSession.SCHEDULED, attendance__isnull=True)
                    .distinct().order_by("date").values_list("date", flat=True))
        if pend:
            oldest = pend[0]
            late = (today - oldest).days > ATTENDANCE_GRACE_DAYS
            items.append({
                "key": f"att:{c.pk}", "category": "attendance", "level": Notification.URGENT if late else Notification.WARN,
                "title": f"{c.code}: attendance not marked for {len(pend)} class{'es' if len(pend) != 1 else ''}",
                "body": f"Oldest from {oldest:%a %d %b}." + (" Overdue." if late else ""),
                "url": f"{pending_url}?course={c.pk}"})
    waiting = (Submission.objects.filter(assignment__course__in=courses, status=Submission.TURNED_IN)
               .select_related("assignment__course").order_by("submitted_at"))
    by_assignment = {}
    for s in waiting:
        by_assignment.setdefault(s.assignment, []).append(s)
    for a, subs in by_assignment.items():
        oldest = subs[0].submitted_at
        late = bool(oldest and timezone.now() - oldest > timedelta(days=GRADING_GRACE_DAYS))
        items.append({
            "key": f"grade:{a.pk}", "category": "grading", "level": Notification.URGENT if late else Notification.WARN,
            "title": f"{a.course.code}: {len(subs)} submission{'s' if len(subs) != 1 else ''} to grade - {a.title}",
            "body": f"Waiting since {_when(oldest)}." if oldest else "",
            "url": reverse("assignment_detail", args=[a.course_id, a.pk])})
    for e in Exam.objects.filter(course__in=courses).exclude(status=Exam.MARKED).select_related("course"):
        if e.date and e.date < today:
            late = (today - e.date).days > MARKS_GRACE_DAYS
            items.append({
                "key": f"marks:{e.pk}", "category": "exam", "level": Notification.URGENT if late else Notification.WARN,
                "title": f"{e.course.code}: enter and release marks - {e.title}",
                "body": f"Exam held on {e.date:%d %b}.", "url": reverse("exam_marks", args=[e.course_id, e.pk])})
        elif e.status == Exam.DRAFT and e.date and e.date <= today + timedelta(days=7):
            items.append({
                "key": f"paper:{e.pk}", "category": "exam", "level": Notification.WARN,
                "title": f"{e.course.code}: finalise the paper - {e.title}",
                "body": f"Exam on {e.date:%a %d %b}; the paper is still a draft.",
                "url": reverse("exam_detail", args=[e.course_id, e.pk])})
    return items, courses


def _student_items(user, term):
    items = []
    courses = list(in_term(Course.objects.filter(enrollments__student=user, archived=False), term).distinct())
    now = timezone.now()
    today = timezone.localdate()
    if courses:
        subs = {s.assignment_id: s for s in Submission.objects.filter(student=user, assignment__course__in=courses)}
        for a in Assignment.objects.filter(course__in=courses, status=Assignment.PUBLISHED, due_at__isnull=False,
                                           due_at__gte=now - timedelta(days=14), due_at__lte=now + timedelta(days=3)).select_related("course"):
            s = subs.get(a.pk)
            if s and s.status in (Submission.TURNED_IN, Submission.RETURNED):
                continue
            late = now > a.due_at
            items.append({
                "key": f"hw:{a.pk}", "category": "classwork", "level": Notification.URGENT if late else Notification.WARN,
                "title": f"{a.course.code}: {'overdue' if late else 'due'} - {a.title}", "body": f"Due {_when(a.due_at)}.",
                "due_at": a.due_at, "url": reverse("assignment_detail", args=[a.course_id, a.pk])})
        for s in Submission.objects.filter(student=user, status=Submission.RETURNED, graded_at__gte=now - timedelta(days=14),
                                           assignment__course__in=courses).select_related("assignment__course"):
            items.append({
                "key": f"graded:{s.pk}", "category": "grading", "level": Notification.INFO, "actionable": False,
                "title": f"{s.assignment.course.code}: graded - {s.assignment.title}",
                "body": f"Score {s.score:g}/{s.assignment.max_points:g}." if s.score is not None and getattr(s.assignment, "max_points", None) else "",
                "url": reverse("assignment_detail", args=[s.assignment.course_id, s.assignment_id])})
        threshold = settings.LMS_ATTENDANCE_THRESHOLD
        for c in courses:
            st = attendance_stats(c, [user]).get(user.pk)
            if st and st["percent"] is not None and st["total"] >= 4 and st["percent"] < threshold:
                items.append({
                    "key": f"lowatt:{c.pk}", "category": "attendance", "level": Notification.WARN,
                    "title": f"{c.code}: your attendance is {st['percent']:g}% (minimum {threshold}%)",
                    "body": f"{st['present']} of {st['total']} classes attended.", "url": reverse("course_detail", args=[c.pk])})
        exams = Exam.objects.filter(course__in=courses, status=Exam.PUBLISHED, date__gte=today,
                                    date__lte=today + timedelta(days=7)).select_related("course")
        for e in exams:
            if e.purpose != Exam.REGULAR and not e.candidates.filter(pk=user.pk).exists():
                continue
            items.append({
                "key": f"exam:{e.pk}", "category": "exam", "level": Notification.INFO, "actionable": False,
                "title": f"{e.course.code}: {e.title} on {e.date:%a %d %b}", "body": f"{e.duration_minutes} min, {e.total_marks:g} marks.",
                "url": reverse("exam_list", args=[e.course_id])})
    sp = getattr(user, "student_profile", None)
    if sp is not None:
        from finance.models import Invoice

        for inv in Invoice.objects.filter(student=sp).prefetch_related("payments"):
            bal = inv.balance
            if bal <= Decimal("0") or not inv.due_date:
                continue
            if inv.due_date < today:
                level = Notification.URGENT
            elif inv.due_date <= today + timedelta(days=15):
                level = Notification.WARN
            else:
                continue
            items.append({
                "key": f"fee:{inv.pk}", "category": "fees", "level": level,
                "title": f"Fees: ₹{bal:,.0f} {'overdue' if level == Notification.URGENT else 'due'} ({inv.academic_year})",
                "body": f"Due {inv.due_date:%d %b %Y}.", "url": reverse("student_detail", args=[sp.pk])})
    return items


def _deadline_items(user, teacher_courses):
    items = []
    course_ids = {c.pk for c in teacher_courses}
    acked = set(DeadlineAck.objects.filter(user=user).values_list("deadline_id", flat=True))
    url = reverse("notification_center")
    for d in _deadlines_for(user):
        due = f"Due {_when(d.due_at)}." if d.due_at else ""
        if d.is_auto and d.audience == Deadline.TEACHERS:
            if not course_ids:
                continue
            for c in deadline_courses(d):
                if c.pk not in course_ids:
                    continue
                done, detail = course_done(d, c)
                if done:
                    continue
                items.append({
                    "key": f"dl:{d.pk}:{c.pk}", "category": "deadline", "level": _level_for_due(d.due_at, d.remind_days),
                    "title": f"{c.code}: {d.title}", "body": f"{due} {detail}.".strip(), "due_at": d.due_at,
                    "url": _deadline_target(d, c)})
            continue
        if d.pk in acked or not deadline_users(d).filter(pk=user.pk).exists():
            continue
        notice = d.kind == Deadline.NOTICE
        items.append({
            "key": f"dl:{d.pk}", "category": "notice" if notice else "deadline",
            "level": Notification.INFO if notice else _level_for_due(d.due_at, d.remind_days), "actionable": not notice,
            "title": d.title, "body": (d.description[:200] + (" " + due if due else "")).strip(), "due_at": d.due_at,
            "url": f"{url}#deadline-{d.pk}"})
    return items


def _deadline_target(deadline, course):
    k = deadline.kind
    if k in (Deadline.PAPER, Deadline.MARKS):
        return reverse("exam_list", args=[course.pk])
    if k == Deadline.ATTENDANCE:
        return reverse("pending_attendance") + f"?course={course.pk}"
    if k == Deadline.PLAN:
        return reverse("course_plan", args=[course.pk])
    if k == Deadline.GRADING:
        return reverse("classwork", args=[course.pk])
    return reverse("course_detail", args=[course.pk])


def _admin_items(user):
    items = []
    for d in Deadline.objects.filter(active=True, due_at__isnull=False):
        level = _level_for_due(d.due_at, d.remind_days)
        if level == Notification.INFO or not (d.is_auto or d.kind == Deadline.CUSTOM):
            continue
        rep = deadline_report(d)
        pending = rep["total"] - rep["done"]
        if pending <= 0:
            continue
        unit = "courses" if rep["by_course"] else "people"
        items.append({
            "key": f"adm-dl:{d.pk}", "category": "admin", "level": level,
            "title": f"{d.title}: {pending} of {rep['total']} {unit} pending",
            "body": f"Due {_when(d.due_at)}.", "due_at": d.due_at, "url": reverse("deadline_detail", args=[d.pk])})
    try:
        from biometric.models import Punch

        unlinked = Punch.objects.filter(person__isnull=True).count()
        if unlinked:
            items.append({"key": "adm-bio", "category": "admin", "level": Notification.INFO,
                          "title": f"{unlinked} biometric punches are not linked to anyone",
                          "body": "Add the device ids to staff or student records.", "url": reverse("biometric")})
    except Exception:  # biometric app not migrated yet
        pass
    return items


def collect(user):
    """Everything that currently needs this user's attention, plus recent updates."""
    term = _active_term()
    items, courses = (_teacher_items(user, term) if user.is_teacher else ([], []))
    if not user.is_teacher:
        items += _student_items(user, term)
    items += _deadline_items(user, courses)
    if user.is_staff or user.is_superuser:
        items += _admin_items(user)
    return items


LEVEL_RANK = {Notification.INFO: 0, Notification.WARN: 1, Notification.URGENT: 2}


def refresh(user):
    """Bring the user's stored notifications in line with collect(): add, update, reopen, resolve."""
    now = timezone.now()
    items = {i["key"]: i for i in collect(user)}
    existing = {n.key: n for n in Notification.objects.filter(user=user)}
    created = 0
    for key, item in items.items():
        fields = {"category": item["category"], "level": item["level"], "title": item["title"][:200],
                  "body": item.get("body", "")[:300], "url": item.get("url", "")[:300], "due_at": item.get("due_at"),
                  "actionable": item.get("actionable", True)}
        n = existing.get(key)
        if n is None:
            Notification.objects.create(user=user, key=key, **fields)
            created += 1
            continue
        escalated = LEVEL_RANK[fields["level"]] > LEVEL_RANK.get(n.level, 0)
        changed = any(getattr(n, f) != v for f, v in fields.items())
        if changed or n.resolved_at:
            for f, v in fields.items():
                setattr(n, f, v)
            if n.resolved_at or escalated:
                n.read_at = None        # back, or more urgent: show it again
                n.emailed_at = None if escalated else n.emailed_at
            n.resolved_at = None
            n.save()
    # attention items whose work is done resolve themselves; plain updates stay until old
    stale = [n.pk for k, n in existing.items() if k not in items and n.resolved_at is None and not k.startswith("msg:")]
    if stale:
        Notification.objects.filter(pk__in=stale).update(resolved_at=now)
    Notification.objects.filter(user=user, resolved_at__lt=now - timedelta(days=60)).delete()
    return created


def maybe_refresh(request):
    """Refresh at most every few minutes per session (called while rendering pages)."""
    stamp = request.session.get("notif_refreshed_at", 0)
    now = timezone.now().timestamp()
    if now - stamp > REFRESH_SECONDS:
        try:
            refresh(request.user)
        finally:
            request.session["notif_refreshed_at"] = now


def unread_count(user):
    return Notification.objects.filter(user=user, resolved_at__isnull=True, read_at__isnull=True).count()


def open_counts(user):
    qs = Notification.objects.filter(user=user, resolved_at__isnull=True)
    return {"unread": qs.filter(read_at__isnull=True).count(), "urgent": qs.filter(level=Notification.URGENT, actionable=True).count()}
