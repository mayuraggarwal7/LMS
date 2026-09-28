import csv
import io
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from courses.access import course_member, course_teacher
from delivery.models import ClassSession
from delivery.services import attendance_stats
from exams.analytics import student_co_profile

from . import grading
from .models import Announcement, Assignment, Submission


class AssignmentForm(forms.ModelForm):
    rubric_template = forms.ChoiceField(required=False, label="Rubric")

    class Meta:
        model = Assignment
        fields = ["title", "kind", "instructions", "unit", "outcomes", "component", "max_points", "due_at",
                  "attachment", "reference_link"]
        widgets = {
            "instructions": forms.Textarea(attrs={"rows": 6}),
            "due_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "outcomes": forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, course=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit"].queryset = course.units.all()
        self.fields["outcomes"].queryset = course.outcomes.all()
        self.fields["outcomes"].label_from_instance = lambda o: f"{o.code} – {o.description[:60]}"
        self.fields["component"].queryset = course.components.exclude(kind="ESE")
        choices = [("", "No rubric (points only)")]
        if self.instance.pk and self.instance.rubric_id:
            choices.append(("keep", f"Keep: {self.instance.rubric.title}"))
            self.initial["rubric_template"] = "keep"
        choices += [(f"r{r.pk}", f"Copy of: {r.title}") for r in course.rubrics.filter(is_template=True)]
        self.fields["rubric_template"].choices = choices


class SubmissionForm(forms.ModelForm):
    class Meta:
        model = Submission
        fields = ["text", "file", "link"]
        widgets = {"text": forms.Textarea(attrs={"rows": 5})}


# --- stream --------------------------------------------------------------------------

@course_member
def stream(request, course):
    if request.method == "POST" and request.is_course_teacher:
        text = (request.POST.get("text") or "").strip()
        if text:
            Announcement.objects.create(course=course, author=request.user, text=text)
            messages.success(request, "Posted to the class stream.")
        return redirect("stream", course.pk)
    feed = []
    for a in course.announcements.select_related("author")[:30]:
        feed.append({"kind": "announcement", "when": a.created_at, "obj": a})
    for a in course.assignments.filter(status=Assignment.PUBLISHED).order_by("-created_at")[:20]:
        feed.append({"kind": "assignment", "when": a.created_at, "obj": a})
    for s in course.sessions.filter(status=ClassSession.COMPLETED, notes_shared=True).exclude(notes="", notes_file="", resource_link="").select_related("plan_item").order_by("-delivered_at")[:20]:
        feed.append({"kind": "notes", "when": s.delivered_at or timezone.now(), "obj": s})
    feed.sort(key=lambda f: f["when"], reverse=True)
    return render(request, "classroom/stream.html", {"course": course, "tab": "stream", "feed": feed})


# --- classwork -----------------------------------------------------------------------

@course_member
def classwork(request, course):
    assignments = course.assignments.select_related("unit", "component", "rubric").prefetch_related("outcomes")
    if not request.is_course_teacher:
        assignments = assignments.filter(status=Assignment.PUBLISHED)
        mine = {s.assignment_id: s for s in Submission.objects.filter(student=request.user, assignment__course=course)}
        rows = [{"a": a, "sub": mine.get(a.pk)} for a in assignments]
    else:
        rows = []
        for a in assignments:
            subs = list(a.submissions.all())
            rows.append({
                "a": a, "turned_in": sum(1 for s in subs if s.status == Submission.TURNED_IN),
                "graded": sum(1 for s in subs if s.status == Submission.RETURNED),
            })
    return render(request, "classroom/classwork.html", {"course": course, "tab": "classwork", "rows": rows})


def _apply_rubric_choice(assignment, choice, course):
    if choice == "keep" or choice is None:
        return
    if not choice:
        assignment.rubric = None
        return
    template = course.rubrics.filter(pk=choice[1:]).first()
    if template:
        outcomes = list(assignment.outcomes.all()) or list(assignment.unit.outcomes.all() if assignment.unit else [])
        assignment.rubric = template.clone(title=f"{assignment.title[:150]} rubric", outcomes=outcomes or None)


@course_teacher
def assignment_edit(request, course, assignment_id=None):
    assignment = get_object_or_404(Assignment, pk=assignment_id, course=course) if assignment_id else None
    form = AssignmentForm(request.POST or None, request.FILES or None, instance=assignment, course=course)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.course = course
        obj.save()
        form.save_m2m()
        _apply_rubric_choice(obj, form.cleaned_data.get("rubric_template"), course)
        if "publish" in request.POST:
            obj.status = Assignment.PUBLISHED
        obj.save()
        messages.success(request, "Assignment published to students." if obj.is_published else "Draft saved.")
        return redirect("assignment_detail", course.pk, obj.pk)
    return render(request, "classroom/assignment_form.html", {"course": course, "tab": "classwork", "form": form, "assignment": assignment})


@course_teacher
@require_POST
def assignment_status(request, course, assignment_id):
    a = get_object_or_404(Assignment, pk=assignment_id, course=course)
    action = request.POST.get("action")
    if action == "publish":
        a.status = Assignment.PUBLISHED
        a.save(update_fields=["status"])
        messages.success(request, "Published - students can now see and submit it.")
    elif action == "unpublish":
        a.status = Assignment.DRAFT
        a.save(update_fields=["status"])
    elif action == "delete":
        a.delete()
        messages.success(request, "Assignment deleted.")
        return redirect("classwork", course.pk)
    return redirect("assignment_detail", course.pk, a.pk)


@course_member
def assignment_detail(request, course, assignment_id):
    a = get_object_or_404(Assignment.objects.select_related("rubric", "component", "unit"), pk=assignment_id, course=course)
    rubric = a.rubric
    criteria = rubric.criteria.prefetch_related("levels").select_related("outcome") if rubric else []
    if not request.is_course_teacher:
        if not a.is_published:
            return redirect("classwork", course.pk)
        sub = grading.get_or_create_submission(a, request.user)
        form = SubmissionForm(request.POST or None, request.FILES or None, instance=sub)
        if request.method == "POST":
            action = request.POST.get("action")
            if action == "unsubmit" and sub.status == Submission.TURNED_IN:
                sub.status = Submission.ASSIGNED
                sub.save(update_fields=["status"])
                messages.info(request, "Unsubmitted - you can edit and turn in again.")
                return redirect("assignment_detail", course.pk, a.pk)
            if sub.status != Submission.RETURNED and form.is_valid():
                sub = form.save(commit=False)
                if action == "turn_in":
                    if not (sub.text or sub.file or sub.link):
                        messages.error(request, "Add an answer, file or link before turning in.")
                        return redirect("assignment_detail", course.pk, a.pk)
                    sub.status = Submission.TURNED_IN
                    sub.submitted_at = timezone.now()
                sub.save()
                messages.success(request, "Turned in!" if action == "turn_in" else "Saved.")
                return redirect("assignment_detail", course.pk, a.pk)
        scores = {cs.criterion_id: cs for cs in sub.criterion_scores.all()} if sub.status == Submission.RETURNED else {}
        rubric_rows = [{"criterion": c, "score": scores.get(c.pk)} for c in criteria]
        return render(request, "classroom/assignment_student.html", {
            "course": course, "tab": "classwork", "a": a, "sub": sub, "form": form, "rubric_rows": rubric_rows,
        })

    # teacher view: submissions table + quick totals entry (score -> rubric distribution)
    students = list(course.students)
    subs = {s.student_id: s for s in a.submissions.all()}
    if request.method == "POST" and request.POST.get("action") == "quick_grade":
        count = 0
        for student in students:
            raw = (request.POST.get(f"score-{student.pk}") or "").strip()
            if raw == "":
                continue
            try:
                value = Decimal(raw)
            except InvalidOperation:
                continue
            sub = subs.get(student.pk) or grading.get_or_create_submission(a, student)
            if sub.score is not None and Decimal(sub.score) == value:
                continue
            grading.set_total_score(sub, value, grader_return=bool(request.POST.get("return")))
            count += 1
        messages.success(request, f"{count} grade(s) saved" + (" and returned to students." if request.POST.get("return") else "."))
        return redirect("assignment_detail", course.pk, a.pk)
    rows = [{"student": s, "sub": subs.get(s.pk)} for s in students]
    return render(request, "classroom/assignment_teacher.html", {
        "course": course, "tab": "classwork", "a": a, "rows": rows, "criteria": criteria,
        "stats": {
            "turned_in": sum(1 for s in subs.values() if s.status == Submission.TURNED_IN),
            "returned": sum(1 for s in subs.values() if s.status == Submission.RETURNED),
            "missing": sum(1 for st in students if st.pk not in subs or subs[st.pk].status == Submission.ASSIGNED),
        },
    })


@course_teacher
def grade_submission(request, course, assignment_id, student_id):
    a = get_object_or_404(Assignment.objects.select_related("rubric"), pk=assignment_id, course=course)
    student = get_object_or_404(course.students, pk=student_id)
    sub = grading.get_or_create_submission(a, student)
    criteria = list(a.rubric.criteria.prefetch_related("levels").select_related("outcome")) if a.rubric else []
    if request.method == "POST":
        feedback = request.POST.get("feedback", "")
        do_return = request.POST.get("action") != "save_draft"
        if request.POST.get("mode") == "total" or not criteria:
            try:
                grading.set_total_score(sub, Decimal(request.POST.get("total") or "0"), feedback, grader_return=do_return)
            except InvalidOperation:
                messages.error(request, "Enter a valid number.")
                return redirect("grade_submission", course.pk, a.pk, student.pk)
        else:
            entries = {}
            for c in criteria:
                level_id = request.POST.get(f"level-{c.pk}") or None
                raw_points = (request.POST.get(f"points-{c.pk}") or "").strip()
                try:
                    points = Decimal(raw_points) if raw_points else None
                except InvalidOperation:
                    points = None
                entries[c.pk] = {"level_id": level_id, "points": points, "comment": request.POST.get(f"comment-{c.pk}", "")}
            grading.apply_rubric_scores(sub, entries, feedback, grader_return=do_return)
        messages.success(request, f"Grade {'returned to' if do_return else 'saved for'} {student.display_name}: {sub.score}/{a.max_points}")
        nxt = _next_student(course, a, student)
        if request.POST.get("next") and nxt:
            return redirect("grade_submission", course.pk, a.pk, nxt.pk)
        return redirect("grade_submission", course.pk, a.pk, student.pk)
    scores = {cs.criterion_id: cs for cs in sub.criterion_scores.all()}
    rows = [{"criterion": c, "score": scores.get(c.pk)} for c in criteria]
    return render(request, "classroom/grade_submission.html", {
        "course": course, "tab": "classwork", "a": a, "sub": sub, "student": student, "rows": rows,
        "next_student": _next_student(course, a, student),
    })


def _next_student(course, assignment, current):
    students = list(course.students)
    ids = [s.pk for s in students]
    if current.pk not in ids:
        return None
    turned_in = set(assignment.submissions.filter(status=Submission.TURNED_IN).values_list("student_id", flat=True))
    after = students[ids.index(current.pk) + 1:]
    return next((s for s in after if s.pk in turned_in), after[0] if after else None)


@course_teacher
@require_POST
def import_grades(request, course, assignment_id):
    """CSV with columns roll_no/username, score -> totals distributed onto the rubric."""
    a = get_object_or_404(Assignment, pk=assignment_id, course=course)
    upload = request.FILES.get("file")
    if not upload:
        messages.error(request, "Choose a CSV file.")
        return redirect("assignment_detail", course.pk, a.pk)
    students = {s.roll_no.lower(): s for s in course.students if s.roll_no}
    students.update({s.username.lower(): s for s in course.students})
    count, skipped = 0, 0
    for row in csv.reader(io.StringIO(upload.read().decode("utf-8-sig"))):
        if len(row) < 2:
            continue
        student = students.get(row[0].strip().lower())
        try:
            value = Decimal(row[-1].strip())
        except InvalidOperation:
            continue  # header
        if not student:
            skipped += 1
            continue
        grading.set_total_score(grading.get_or_create_submission(a, student), value, grader_return=False)
        count += 1
    messages.success(request, f"Imported {count} grade(s) as drafts." + (f" {skipped} row(s) did not match a student." if skipped else ""))
    return redirect("assignment_detail", course.pk, a.pk)


@course_teacher
@require_POST
def return_all(request, course, assignment_id):
    a = get_object_or_404(Assignment, pk=assignment_id, course=course)
    n = a.submissions.filter(score__isnull=False).exclude(status=Submission.RETURNED).update(status=Submission.RETURNED)
    messages.success(request, f"Returned {n} graded submission(s) to students.")
    return redirect("assignment_detail", course.pk, a.pk)


# --- gradebook -------------------------------------------------------------------------

@course_teacher
def gradebook(request, course):
    book = grading.build_gradebook(course)
    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{course.code}-gradebook.csv"'
        writer = csv.writer(response)
        header = ["Roll no", "Name"]
        for col in book["columns"]:
            for item in col["items"]:
                header.append(f"{col['component'].name}: {item['obj']} (/{item['max']})")
            header.append(f"{col['component'].name} (/{col['component'].max_marks})")
        writer.writerow(header + ["Final %", "Grade", "Grade point"])
        for row in book["rows"]:
            line = [row["student"].roll_no, row["student"].display_name]
            for comp in row["components"]:
                line += [c["score"] if c and c["score"] is not None else ("AB" if c else "") for c in comp["cells"]]
                line.append(comp["marks"] if comp["marks"] is not None else "")
            line += [f"{row['final_percent']:.2f}" if row["final_percent"] is not None else "", row["grade"], row["grade_point"] or ""]
            writer.writerow(line)
        return response
    return render(request, "classroom/gradebook.html", {"course": course, "tab": "grades", "book": book})


@course_member
def my_grades(request, course):
    student = request.user
    if request.is_course_teacher:
        student = get_object_or_404(course.students, pk=request.GET.get("student"))
    book = grading.build_gradebook(course, [student], released_only=not request.is_course_teacher)
    row = book["rows"][0] if book["rows"] else None
    subs = Submission.objects.filter(assignment__course=course, student=student, status=Submission.RETURNED).select_related("assignment")
    return render(request, "classroom/my_grades.html", {
        "course": course, "tab": "grades", "book": book, "row": row, "student": student, "subs": subs,
        "co_profile": student_co_profile(course, student, released_only=not request.is_course_teacher),
    })


SORT_KEYS = {
    "roll": lambda st: (st.roll_no or "~", st.display_name.lower()),
    "name": lambda st: st.display_name.lower(),
    "prn": lambda st: (st.prn or "~", st.display_name.lower()),
}


@course_teacher
def cie_report(request, course):
    """Consolidated CIE (continuous internal evaluation) and attendance report.

    One row per student: every internal item (NE = not entered, AB = absent), each
    component's bucket marks, total CIE out of the internal maximum and attendance %.
    """
    sort = request.GET.get("sort", "roll") if request.GET.get("sort") in SORT_KEYS else "roll"
    students = sorted(course.students, key=SORT_KEYS[sort])
    book = grading.build_gradebook(course, students)
    attendance = attendance_stats(course, students)
    include_ese = request.GET.get("ese") == "1"
    columns = [c for c in book["columns"] if include_ese or c["component"].is_internal]
    keep = {c["component"].pk for c in columns}
    cie_max = sum((c["component"].max_marks for c in columns if c["component"].is_internal), Decimal("0"))
    rows = []
    for i, row in enumerate(book["rows"], start=1):
        comps = [c for c in row["components"] if c["component"].pk in keep]
        internal = [c for c in comps if c["component"].is_internal]
        entered = [c["marks"] for c in internal if c["marks"] is not None]
        rows.append({
            "sl": i, "student": row["student"], "components": comps,
            "cie_total": sum(entered, Decimal("0")) if entered else None,
            "attendance": attendance.get(row["student"].pk),
        })
    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{course.code}-CIE-attendance.csv"'
        writer = csv.writer(response)
        header = ["SL No.", "Student name", "Roll no.", "PRN no."]
        for col in columns:
            for item in col["items"]:
                header.append(f"{col['component'].name}: {item['obj']} (/{item['max']:g})")
            header.append(f"{col['component'].name} bucket (/{col['component'].max_marks:g})")
        header += [f"Total CIE (/{cie_max:g})", "Attended", "Conducted", "Attendance %"]
        writer.writerow(header)
        for r in rows:
            line = [r["sl"], r["student"].display_name, r["student"].roll_no, r["student"].prn]
            for comp in r["components"]:
                for cell in comp["cells"]:
                    line.append("NE" if not cell else ("AB" if cell["score"] is None else cell["score"]))
                line.append("NE" if comp["marks"] is None else comp["marks"])
            att = r["attendance"] or {}
            line += ["NE" if r["cie_total"] is None else r["cie_total"], att.get("present", ""), att.get("total", ""),
                     "" if att.get("percent") is None else att["percent"]]
            writer.writerow(line)
        return response
    return render(request, "classroom/cie_report.html", {
        "course": course, "tab": "grades", "columns": columns, "rows": rows, "sort": sort, "cie_max": cie_max,
        "include_ese": include_ese,
    })
