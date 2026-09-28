from django import forms

from accounts.models import YEAR_CHOICES

from .models import Course, CourseOutcome, PlanItem, Rubric

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class SyllabusUploadForm(forms.Form):
    syllabus_file = forms.FileField(required=False, help_text="PDF, Word (.docx) or text file")
    syllabus_text = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 8, "placeholder": "...or paste the syllabus text here"}))
    year = forms.TypedChoiceField(choices=YEAR_CHOICES, coerce=int, initial=1, label="Year of students")
    semester = forms.IntegerField(min_value=1, max_value=8, initial=1)
    department = forms.CharField(required=False)
    division = forms.CharField(required=False, label="Division / section")
    academic_year = forms.CharField(required=False, initial="2026-27")
    start_date = forms.DateField(required=False, widget=DATE, label="Semester starts")
    end_date = forms.DateField(required=False, widget=DATE, label="Teaching ends")
    use_ai = forms.BooleanField(required=False, initial=True, label="Use AI assistance for parsing (when configured)")

    def clean(self):
        data = super().clean()
        if not data.get("syllabus_file") and not (data.get("syllabus_text") or "").strip():
            raise forms.ValidationError("Upload a syllabus file or paste its text.")
        if data.get("start_date") and data.get("end_date") and data["end_date"] <= data["start_date"]:
            raise forms.ValidationError("The end date must be after the start date.")
        return data


class CourseReviewForm(forms.Form):
    code = forms.CharField(max_length=30)
    title = forms.CharField(max_length=200)
    department = forms.CharField(required=False)
    year = forms.TypedChoiceField(choices=YEAR_CHOICES, coerce=int)
    semester = forms.IntegerField(min_value=1, max_value=8)
    division = forms.CharField(required=False)
    academic_year = forms.CharField(required=False)
    credits = forms.DecimalField(max_digits=4, decimal_places=1, initial=3)
    lecture_hours = forms.IntegerField(min_value=0, max_value=10, initial=3, label="Lecture hrs/week")
    tutorial_hours = forms.IntegerField(min_value=0, max_value=10, initial=0, label="Tutorial hrs/week")
    practical_hours = forms.IntegerField(min_value=0, max_value=10, initial=0, label="Practical hrs/week")
    start_date = forms.DateField(required=False, widget=DATE)
    end_date = forms.DateField(required=False, widget=DATE)
    prerequisites = forms.CharField(required=False)
    outcomes_text = forms.CharField(
        label="Course outcomes", widget=forms.Textarea(attrs={"rows": 6}),
        help_text="One per line, e.g. CO1: Explain ...", required=False,
    )
    units_text = forms.CharField(
        label="Units & topics", widget=forms.Textarea(attrs={"rows": 18, "class": "mono"}),
        help_text="'Unit 1: Title | hours' followed by one '- topic' per line",
    )
    textbooks_text = forms.CharField(label="Textbooks", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    references_text = forms.CharField(label="References & online resources", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    experiments_text = forms.CharField(label="Lab experiments", required=False, widget=forms.Textarea(attrs={"rows": 5}),
                                       help_text="One experiment per line")
    opt_plan = forms.BooleanField(required=False, initial=True, label="Learning plan (session-by-session)")
    opt_scheme = forms.BooleanField(required=False, initial=True, label="Assessment scheme (ISE / MSE / ESE)")
    opt_rubrics = forms.BooleanField(required=False, initial=True, label="Rubric library")
    opt_questions = forms.BooleanField(required=False, initial=True, label="Question bank")
    opt_assignments = forms.BooleanField(required=False, initial=True, label="Draft assignments per unit")
    opt_exams = forms.BooleanField(required=False, initial=True, label="Draft MSE & ESE papers")


class CourseSettingsForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ["code", "title", "department", "year", "semester", "division", "academic_year", "credits",
                  "lecture_hours", "tutorial_hours", "practical_hours", "start_date", "end_date", "description",
                  "prerequisites", "archived"]
        widgets = {"start_date": DATE, "end_date": DATE, "description": forms.Textarea(attrs={"rows": 3}),
                   "prerequisites": forms.Textarea(attrs={"rows": 2})}


class OutcomeForm(forms.ModelForm):
    class Meta:
        model = CourseOutcome
        fields = ["code", "description", "bloom_level", "target_percent"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}


OutcomeFormSet = forms.inlineformset_factory(Course, CourseOutcome, form=OutcomeForm, extra=1, can_delete=True)


class PlanItemForm(forms.ModelForm):
    class Meta:
        model = PlanItem
        fields = ["title", "session_type", "unit", "topics", "outcomes", "objective", "bloom_level", "duration_minutes",
                  "pedagogy", "tools", "pre_class", "in_class", "post_class", "resources"]
        widgets = {
            "objective": forms.Textarea(attrs={"rows": 2}), "tools": forms.Textarea(attrs={"rows": 3}),
            "pre_class": forms.Textarea(attrs={"rows": 2}), "in_class": forms.Textarea(attrs={"rows": 2}),
            "post_class": forms.Textarea(attrs={"rows": 2}), "resources": forms.Textarea(attrs={"rows": 2}),
            "topics": forms.CheckboxSelectMultiple, "outcomes": forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, course=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit"].queryset = course.units.all()
        self.fields["topics"].queryset = self.fields["topics"].queryset.filter(unit__course=course).select_related("unit")
        self.fields["outcomes"].queryset = course.outcomes.all()
        self.fields["topics"].label_from_instance = lambda t: f"U{t.unit.number}: {t.title}"
        self.fields["outcomes"].label_from_instance = lambda o: f"{o.code} – {o.description[:70]}"


class RubricMetaForm(forms.ModelForm):
    class Meta:
        model = Rubric
        fields = ["title", "kind", "description", "is_template"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}


class AddStudentsForm(forms.Form):
    rows = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 6, "class": "mono", "placeholder": "roll_no, full name, email\n22CE001, Asha Patil, asha@college.edu"}),
        help_text="One student per line: roll number, name, email (existing usernames/emails are linked, new ones get an account).",
    )
