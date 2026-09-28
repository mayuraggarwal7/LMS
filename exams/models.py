from decimal import Decimal

from django.conf import settings
from django.db import models

from courses.models import BLOOM_LEVELS, Course, CourseOutcome, Topic, Unit


class AssessmentComponent(models.Model):
    """A bucket of the evaluation scheme, e.g. ISE-1, MSE, ISE-2, Term work, ESE."""

    KIND_CHOICES = [
        ("ISE", "In-semester evaluation (continuous)"),
        ("MSE", "Mid-semester exam"),
        ("ESE", "End-semester exam"),
        ("TW", "Term work / lab"),
        ("ORAL", "Oral / practical exam"),
    ]
    AGG_CHOICES = [
        ("average", "Average of items"),
        ("sum", "Sum of items"),
        ("best", "Best N items"),
    ]
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="components")
    name = models.CharField(max_length=60)
    kind = models.CharField(max_length=5, choices=KIND_CHOICES, default="ISE")
    weight = models.DecimalField("Weight (% of final)", max_digits=5, decimal_places=2)
    max_marks = models.DecimalField("Reported out of", max_digits=6, decimal_places=2, default=Decimal("20"))
    aggregation = models.CharField(max_length=10, choices=AGG_CHOICES, default="average")
    best_of = models.PositiveSmallIntegerField(default=0, help_text="Used when aggregation is 'Best N'")
    min_pass_percent = models.PositiveSmallIntegerField(default=0, help_text="Minimum % needed in this component to pass")
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "pk"]

    def __str__(self):
        return self.name

    @property
    def is_internal(self):
        return self.kind != "ESE"


class Question(models.Model):
    QTYPES = [("short", "Short answer"), ("long", "Descriptive"), ("numerical", "Numerical"), ("mcq", "MCQ"), ("design", "Design / open-ended")]
    DIFFICULTY = [("easy", "Easy"), ("medium", "Medium"), ("hard", "Hard")]
    SOURCES = [("manual", "Teacher"), ("generated", "Generated from syllabus"), ("ai", "AI assisted")]

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="questions")
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, blank=True, related_name="questions")
    topic = models.ForeignKey(Topic, on_delete=models.SET_NULL, null=True, blank=True)
    outcome = models.ForeignKey(CourseOutcome, on_delete=models.SET_NULL, null=True, blank=True)
    text = models.TextField()
    marks = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("5"))
    bloom_level = models.PositiveSmallIntegerField(choices=BLOOM_LEVELS, default=2)
    difficulty = models.CharField(max_length=6, choices=DIFFICULTY, default="medium")
    qtype = models.CharField(max_length=10, choices=QTYPES, default="long")
    answer_key = models.TextField(blank=True)
    source = models.CharField(max_length=10, choices=SOURCES, default="manual")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["unit__number", "marks", "bloom_level", "pk"]

    def __str__(self):
        return self.text[:80]


class Exam(models.Model):
    DRAFT, PUBLISHED, MARKED = "draft", "published", "marked"
    STATUS_CHOICES = [(DRAFT, "Draft"), (PUBLISHED, "Scheduled"), (MARKED, "Marks released")]
    REGULAR, REEXAM, ADDITIONAL = "regular", "reexam", "additional"
    PURPOSE_CHOICES = [
        (REGULAR, "Regular assessment"),
        (REEXAM, "Re-exam (for failed / absent students)"),
        (ADDITIONAL, "Additional / make-up assessment"),
    ]
    REPLACE, BEST = "replace", "best"
    POLICY_CHOICES = [(REPLACE, "Replaces the original score"), (BEST, "Better of original and new score")]

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="exams")
    component = models.ForeignKey(AssessmentComponent, on_delete=models.SET_NULL, null=True, blank=True, related_name="exams")
    title = models.CharField(max_length=200)
    date = models.DateField(null=True, blank=True)
    duration_minutes = models.PositiveSmallIntegerField(default=60)
    total_marks = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("20"))
    units = models.ManyToManyField(Unit, blank=True)
    instructions = models.TextField(
        blank=True, default="1. All questions are compulsory unless stated otherwise.\n2. Figures to the right indicate full marks.\n3. Assume suitable data wherever necessary."
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    purpose = models.CharField(max_length=10, choices=PURPOSE_CHOICES, default=REGULAR)
    policy = models.CharField(max_length=10, choices=POLICY_CHOICES, default=REPLACE)
    replaces_exam = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="retakes",
                                      help_text="The exam this re-exam / make-up stands in for")
    replaces_assignment = models.ForeignKey("classroom.Assignment", on_delete=models.CASCADE, null=True, blank=True,
                                            related_name="retakes", help_text="The assignment this make-up stands in for")
    candidates = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="retake_exams",
                                        help_text="Students registered for this re-exam / additional assessment")
    term = models.ForeignKey("courses.AcademicTerm", on_delete=models.SET_NULL, null=True, blank=True,
                             help_text="e.g. the re-exam term it is conducted in")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "pk"]

    def __str__(self):
        return self.title

    @property
    def is_retake(self):
        return self.purpose != self.REGULAR

    @property
    def original(self):
        return self.replaces_exam or self.replaces_assignment

    @property
    def paper_total(self):
        return sum((q.marks for q in self.paper.all()), Decimal("0"))


class ExamQuestion(models.Model):
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="paper")
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    section = models.CharField(max_length=40, blank=True)
    label = models.CharField(max_length=10)
    marks = models.DecimalField(max_digits=5, decimal_places=2)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "pk"]

    def __str__(self):
        return f"{self.label} ({self.marks})"


class ExamMark(models.Model):
    exam_question = models.ForeignKey(ExamQuestion, on_delete=models.CASCADE, related_name="marks_obtained")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="exam_marks")
    marks = models.DecimalField(max_digits=5, decimal_places=2)

    class Meta:
        unique_together = [("exam_question", "student")]


class ExamAbsence(models.Model):
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="absentees")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        unique_together = [("exam", "student")]
