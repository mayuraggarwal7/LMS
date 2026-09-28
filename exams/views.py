import csv
import io
import random
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from courses.access import course_member, course_teacher
from courses.forms import DATE
from courses.models import Course

from . import analytics, services
from .models import AssessmentComponent, Exam, ExamAbsence, ExamMark, ExamQuestion, Question


class ComponentForm(forms.ModelForm):
    class Meta:
        model = AssessmentComponent
        fields = ["name", "kind", "weight", "max_marks", "aggregation", "best_of", "min_pass_percent", "order"]


ComponentFormSet = forms.inlineformset_factory(Course, AssessmentComponent, form=ComponentForm, extra=1, can_delete=True)


class ExamForm(forms.Form):
    title = forms.CharField(max_length=200)
    component = forms.ModelChoiceField(queryset=AssessmentComponent.objects.none(), required=False)
    date = forms.DateField(required=False, widget=DATE)
    duration_minutes = forms.IntegerField(min_value=10, initial=60)
    total_marks = forms.DecimalField(min_value=2, initial=20)
    units = forms.ModelMultipleChoiceField(queryset=None, widget=forms.CheckboxSelectMultiple, required=False,
                                           help_text="Leave empty for the whole syllabus")
    short_pct = forms.IntegerField(min_value=0, max_value=100, initial=20, label="Short (2-mark) %")
    medium_pct = forms.IntegerField(min_value=0, max_value=100, initial=40, label="Medium (5-mark) %")
    long_pct = forms.IntegerField(min_value=0, max_value=100, initial=40, label="Long (10-mark) %")

    def __init__(self, *args, course=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["component"].queryset = course.components.all()
        self.fields["units"].queryset = course.units.all()

    def clean(self):
        data = super().clean()
        if sum(data.get(k) or 0 for k in ("short_pct", "medium_pct", "long_pct")) != 100:
            raise forms.ValidationError("Blueprint percentages must add up to 100.")
        return data


class QuestionForm(forms.ModelForm):
    class Meta:
        model = Question
        fields = ["unit", "outcome", "text", "marks", "bloom_level", "difficulty", "qtype", "answer_key"]
        widgets = {"text": forms.Textarea(attrs={"rows": 3}), "answer_key": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, course=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit"].queryset = course.units.all()
        self.fields["outcome"].queryset = course.outcomes.all()


@course_teacher
def scheme(request, course):
    formset = ComponentFormSet(request.POST or None, instance=course, prefix="comp")
    if request.method == "POST":
        if "use_default" in request.POST:
            if not course.components.exists():
                services.create_default_scheme(course)
                messages.success(request, "Default scheme created.")
            return redirect("exam_scheme", course.pk)
        if formset.is_valid():
            formset.save()
            total = sum(c.weight for c in course.components.all())
            if total != 100:
                messages.warning(request, f"Weights add up to {total}%, not 100%. Final marks are normalised to the weights used.")
            else:
                messages.success(request, "Assessment scheme saved.")
            return redirect("exam_scheme", course.pk)
    components = course.components.prefetch_related("assignments", "exams")
    return render(request, "exams/scheme.html", {
        "course": course, "tab": "exams", "formset": formset, "components": components,
        "total_weight": sum(c.weight for c in components),
    })


@course_member
def exam_list(request, course):
    exams = course.exams.select_related("component").prefetch_related("units")
    if not request.is_course_teacher:
        exams = exams.exclude(status=Exam.DRAFT).exclude(~Q(purpose=Exam.REGULAR) & ~Q(candidates=request.user)).distinct()
    rows = [{"exam": e, "summary": analytics.marks_summary(e) if request.is_course_teacher else None} for e in exams]
    return render(request, "exams/exam_list.html", {
        "course": course, "tab": "exams", "rows": rows, "components": course.components.all(),
        "question_count": course.questions.count(),
    })


@course_teacher
def exam_new(request, course):
    form = ExamForm(request.POST or None, course=course, initial={"component": request.GET.get("component")})
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        exam, warnings = services.create_exam(
            course, cd["component"], cd["title"], cd["total_marks"], cd["units"] or course.units.all(),
            cd["duration_minutes"], cd["date"],
            {"short": cd["short_pct"], "medium": cd["medium_pct"], "long": cd["long_pct"]},
        )
        for w in warnings:
            messages.warning(request, w)
        messages.success(request, "Question paper generated from the question bank. Review and adjust below.")
        return redirect("exam_detail", course.pk, exam.pk)
    return render(request, "exams/exam_new.html", {"course": course, "tab": "exams", "form": form})


@course_member
def exam_detail(request, course, exam_id):
    exam = get_object_or_404(Exam, pk=exam_id, course=course)
    if not request.is_course_teacher:
        if exam.is_retake and not exam.candidates.filter(pk=request.user.pk).exists():
            return redirect("exam_list", course.pk)
        return _student_exam(request, course, exam)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "regenerate":
            for w in services.build_paper(exam, seed=random.randint(0, 10**6)):
                messages.warning(request, w)
            messages.success(request, "Paper regenerated.")
        elif action == "remove":
            exam.paper.filter(pk=request.POST.get("eq")).delete()
        elif action == "add":
            q = get_object_or_404(Question, pk=request.POST.get("question"), course=course)
            last = exam.paper.order_by("-order").first()
            ExamQuestion.objects.create(exam=exam, question=q, label=f"Q{exam.paper.count() + 1}", marks=q.marks,
                                        order=(last.order + 1) if last else 1, section=last.section if last else "")
        elif action == "labels":
            for eq in exam.paper.all():
                label = request.POST.get(f"label-{eq.pk}")
                marks = request.POST.get(f"marks-{eq.pk}")
                if label:
                    eq.label = label[:10]
                try:
                    eq.marks = Decimal(marks)
                except (InvalidOperation, TypeError):
                    pass
                eq.save()
        elif action == "meta":
            exam.title = request.POST.get("title") or exam.title
            exam.instructions = request.POST.get("instructions", exam.instructions)
            exam.status = request.POST.get("status") if request.POST.get("status") in dict(Exam.STATUS_CHOICES) else exam.status
            exam.save()
            messages.success(request, "Exam updated.")
        elif action == "delete":
            exam.delete()
            messages.success(request, "Exam deleted.")
            return redirect("exam_list", course.pk)
        return redirect("exam_detail", course.pk, exam.pk)
    paper = exam.paper.select_related("question__unit", "question__outcome")
    used = set(paper.values_list("question_id", flat=True))
    bank = course.questions.filter(unit__in=exam.units.all() or course.units.all()).exclude(pk__in=used).select_related("unit", "outcome")
    return render(request, "exams/exam_detail.html", {
        "course": course, "tab": "exams", "exam": exam, "paper": paper, "bank": bank,
        "bloom": services.bloom_distribution(exam), "cos": services.co_distribution(exam),
        "summary": analytics.marks_summary(exam), "analysis": analytics.question_analysis(exam),
    })


def _student_exam(request, course, exam):
    paper = list(exam.paper.select_related("question__outcome"))
    marks = {m.exam_question_id: m.marks for m in ExamMark.objects.filter(exam_question__exam=exam, student=request.user)}
    show_marks = exam.status == Exam.MARKED
    rows = [{"eq": eq, "marks": marks.get(eq.pk)} for eq in paper]
    total = sum((m for m in marks.values()), Decimal("0")) if marks else None
    absent = ExamAbsence.objects.filter(exam=exam, student=request.user).exists()
    return render(request, "exams/exam_student.html", {
        "course": course, "tab": "exams", "exam": exam, "rows": rows, "total": total, "show_marks": show_marks, "absent": absent,
    })


@course_teacher
def exam_print(request, course, exam_id):
    exam = get_object_or_404(Exam, pk=exam_id, course=course)
    sections = []
    for eq in exam.paper.select_related("question__outcome"):
        if not sections or sections[-1]["name"] != eq.section:
            sections.append({"name": eq.section, "items": []})
        sections[-1]["items"].append(eq)
    return render(request, "exams/exam_print.html", {"course": course, "exam": exam, "sections": sections,
                                                     "answers": request.GET.get("answers") == "1"})


@course_teacher
def exam_marks(request, course, exam_id):
    exam = get_object_or_404(Exam, pk=exam_id, course=course)
    paper = list(exam.paper.all())
    students = list(exam.candidates.all()) if exam.is_retake else list(course.students)
    if request.method == "POST":
        if request.FILES.get("file"):
            count = _import_marks(exam, paper, students, request.FILES["file"])
            messages.success(request, f"Imported marks for {count} student(s).")
            return redirect("exam_marks", course.pk, exam.pk)
        with transaction.atomic():
            for s in students:
                absent = bool(request.POST.get(f"ab-{s.pk}"))
                if absent:
                    ExamAbsence.objects.get_or_create(exam=exam, student=s)
                    ExamMark.objects.filter(exam_question__exam=exam, student=s).delete()
                    continue
                ExamAbsence.objects.filter(exam=exam, student=s).delete()
                total_raw = (request.POST.get(f"total-{s.pk}") or "").strip()
                any_q = False
                for eq in paper:
                    raw = (request.POST.get(f"m-{s.pk}-{eq.pk}") or "").strip()
                    if raw == "":
                        continue
                    try:
                        value = max(Decimal("0"), min(Decimal(raw), eq.marks))
                    except InvalidOperation:
                        continue
                    any_q = True
                    ExamMark.objects.update_or_create(exam_question=eq, student=s, defaults={"marks": value})
                if not any_q and total_raw:
                    try:
                        analytics.distribute_exam_total(exam, s, Decimal(total_raw))
                    except InvalidOperation:
                        pass
            if request.POST.get("release"):
                exam.status = Exam.MARKED
                exam.save(update_fields=["status"])
        messages.success(request, "Marks saved." + (" Released to students." if request.POST.get("release") else ""))
        return redirect("exam_marks", course.pk, exam.pk)
    marks = {(m.student_id, m.exam_question_id): m.marks for m in ExamMark.objects.filter(exam_question__exam=exam)}
    absent = set(exam.absentees.values_list("student_id", flat=True))
    totals = analytics.exam_totals(exam)
    rows = [{"student": s, "cells": [(eq, marks.get((s.pk, eq.pk))) for eq in paper], "total": totals.get(s.pk),
             "absent": s.pk in absent} for s in students]
    return render(request, "exams/exam_marks.html", {"course": course, "tab": "exams", "exam": exam, "paper": paper, "rows": rows})


def _import_marks(exam, paper, students, upload):
    """CSV: roll_no/username, then either one column per question (in paper order) or a single total."""
    lookup = {s.roll_no.lower(): s for s in students if s.roll_no}
    lookup.update({s.username.lower(): s for s in students})
    count = 0
    for row in csv.reader(io.StringIO(upload.read().decode("utf-8-sig"))):
        if not row:
            continue
        student = lookup.get(row[0].strip().lower())
        if not student:
            continue
        values = [v.strip() for v in row[1:] if v.strip() != ""]
        # skip a name column if present
        if values and not _is_number(values[0]):
            values = values[1:]
        if len(values) == 1 and values[0].upper() != "AB":
            analytics.distribute_exam_total(exam, student, Decimal(values[0]))
        elif values and values[0].upper() == "AB":
            ExamAbsence.objects.get_or_create(exam=exam, student=student)
        else:
            for eq, v in zip(paper, values):
                if _is_number(v):
                    ExamMark.objects.update_or_create(exam_question=eq, student=student,
                                                      defaults={"marks": max(Decimal("0"), min(Decimal(v), eq.marks))})
        count += 1
    return count


def _is_number(v):
    try:
        Decimal(v)
        return True
    except InvalidOperation:
        return False


@course_teacher
def question_bank(request, course):
    form = QuestionForm(request.POST or None, course=course)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "generate":
            units = course.units.filter(pk__in=request.POST.getlist("units")) or course.units.all()
            n = services.generate_question_bank(course, units)
            messages.success(request, f"Generated {n} questions.")
            return redirect("question_bank", course.pk)
        if action == "delete":
            course.questions.filter(pk__in=request.POST.getlist("selected")).delete()
            messages.success(request, "Selected questions deleted.")
            return redirect("question_bank", course.pk)
        if form.is_valid():
            q = form.save(commit=False)
            q.course = course
            q.source = "manual"
            q.save()
            messages.success(request, "Question added.")
            return redirect("question_bank", course.pk)
    qs = course.questions.select_related("unit", "outcome")
    for key in ("unit", "bloom_level", "marks"):
        if request.GET.get(key):
            qs = qs.filter(**{f"{key}__number" if key == "unit" else key: request.GET[key]})
    return render(request, "exams/question_bank.html", {
        "course": course, "tab": "exams", "questions": qs, "form": form, "units": course.units.all(),
        "filters": request.GET,
    })


@course_teacher
def question_edit(request, course, question_id):
    q = get_object_or_404(Question, pk=question_id, course=course)
    form = QuestionForm(request.POST or None, instance=q, course=course)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Question updated.")
        return redirect("question_bank", course.pk)
    return render(request, "exams/question_edit.html", {"course": course, "tab": "exams", "form": form, "q": q})


@course_teacher
def co_attainment(request, course):
    report = analytics.co_attainment(course)
    return render(request, "exams/co_attainment.html", {"course": course, "tab": "attainment", "report": report})



class RetakeForm(forms.Form):
    purpose = forms.ChoiceField(choices=[c for c in Exam.PURPOSE_CHOICES if c[0] != Exam.REGULAR], widget=forms.RadioSelect)
    policy = forms.ChoiceField(choices=Exam.POLICY_CHOICES, widget=forms.RadioSelect)
    title = forms.CharField(max_length=200)
    date = forms.DateField(required=False, widget=DATE)
    duration_minutes = forms.IntegerField(min_value=10)
    total_marks = forms.DecimalField(min_value=2)
    term = forms.ModelChoiceField(queryset=None, required=False, help_text="Term it is conducted in (e.g. the re-exam term)")
    candidates = forms.ModelMultipleChoiceField(queryset=None, widget=forms.CheckboxSelectMultiple, required=False)

    def __init__(self, *args, course=None, **kwargs):
        from courses.models import AcademicTerm

        super().__init__(*args, **kwargs)
        self.fields["term"].queryset = AcademicTerm.objects.all()
        self.fields["candidates"].queryset = course.students
        self.fields["candidates"].label_from_instance = lambda u: str(u)

    def clean_candidates(self):
        if not self.cleaned_data["candidates"]:
            raise forms.ValidationError("Select at least one student.")
        return self.cleaned_data["candidates"]


@course_teacher
def retake_new(request, course):
    """Create a re-exam or an additional / make-up assessment for an exam or an assignment."""
    from classroom.models import Assignment

    from courses.models import AcademicTerm

    if request.GET.get("assignment") or request.POST.get("assignment"):
        original = get_object_or_404(Assignment, pk=request.GET.get("assignment") or request.POST.get("assignment"), course=course)
    else:
        original = get_object_or_404(Exam, pk=request.GET.get("exam") or request.POST.get("exam"), course=course, purpose=Exam.REGULAR)
    is_assignment = isinstance(original, Assignment)
    purpose = request.GET.get("purpose") or (Exam.ADDITIONAL if is_assignment or (original.component and original.component.is_internal) else Exam.REEXAM)
    suggested = services.retake_candidates(original, purpose)
    reexam_term = AcademicTerm.objects.filter(kind=AcademicTerm.REEXAM).first() if purpose == Exam.REEXAM else None
    initial = {
        "purpose": purpose, "policy": Exam.REPLACE if purpose == Exam.REEXAM else Exam.BEST,
        "title": f"{'Re-exam' if purpose == Exam.REEXAM else 'Additional assessment'}: {original.title}",
        "duration_minutes": 60 if is_assignment else original.duration_minutes,
        "total_marks": original.max_points if is_assignment else original.total_marks,
        "candidates": suggested, "term": reexam_term,
    }
    form = RetakeForm(request.POST or None, course=course, initial=initial)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        exam, warnings = services.create_retake(original, cd["purpose"], cd["policy"], cd["candidates"], cd["title"],
                                                cd["date"], cd["total_marks"], cd["term"], cd["duration_minutes"])
        for w in warnings:
            messages.warning(request, w)
        messages.success(request, f"{exam.get_purpose_display().split(' (')[0]} created for {exam.candidates.count()} student(s) "
                                  "with a fresh paper. Enter marks after it is held - the gradebook updates automatically.")
        return redirect("exam_detail", course.pk, exam.pk)
    return render(request, "exams/retake_new.html", {
        "course": course, "tab": "exams", "form": form, "original": original, "is_assignment": is_assignment,
        "suggested": suggested,
    })
