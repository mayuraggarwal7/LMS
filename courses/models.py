import secrets
import string
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.urls import reverse

from accounts.models import YEAR_CHOICES

BLOOM_LEVELS = [
    (1, "L1 Remember"),
    (2, "L2 Understand"),
    (3, "L3 Apply"),
    (4, "L4 Analyze"),
    (5, "L5 Evaluate"),
    (6, "L6 Create"),
]
BLOOM_SHORT = {1: "Remember", 2: "Understand", 3: "Apply", 4: "Analyze", 5: "Evaluate", 6: "Create"}

SESSION_TYPES = [("lecture", "Lecture"), ("tutorial", "Tutorial"), ("lab", "Lab / Practical")]


def generate_join_code():
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(7))


class Course(models.Model):
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="courses_taught")
    co_teachers = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="courses_co_taught")
    code = models.CharField(max_length=30)
    title = models.CharField(max_length=200)
    department = models.CharField(max_length=120, blank=True)
    year = models.PositiveSmallIntegerField(choices=YEAR_CHOICES, default=1)
    semester = models.PositiveSmallIntegerField(default=1)
    division = models.CharField("Division / section", max_length=30, blank=True)
    academic_year = models.CharField(max_length=20, blank=True, help_text="e.g. 2026-27")
    credits = models.DecimalField(max_digits=4, decimal_places=1, default=Decimal("3"))
    lecture_hours = models.PositiveSmallIntegerField("Lecture hrs/week", default=3)
    tutorial_hours = models.PositiveSmallIntegerField("Tutorial hrs/week", default=0)
    practical_hours = models.PositiveSmallIntegerField("Practical hrs/week", default=0)
    description = models.TextField(blank=True)
    prerequisites = models.TextField(blank=True)
    syllabus_file = models.FileField(upload_to="syllabi/", blank=True)
    syllabus_text = models.TextField(blank=True)
    join_code = models.CharField(max_length=10, unique=True, default=generate_join_code)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.code} {self.title}"

    def get_absolute_url(self):
        return reverse("course_detail", args=[self.pk])

    def is_teacher(self, user):
        if not user.is_authenticated:
            return False
        return user.is_superuser or self.teacher_id == user.pk or self.co_teachers.filter(pk=user.pk).exists()

    def is_member(self, user):
        return self.is_teacher(user) or self.enrollments.filter(student=user).exists()

    @property
    def students(self):
        from accounts.models import User

        return User.objects.filter(enrollments__course=self).order_by("roll_no", "first_name", "username")


class CourseOutcome(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="outcomes")
    code = models.CharField(max_length=10)
    description = models.TextField()
    bloom_level = models.PositiveSmallIntegerField(choices=BLOOM_LEVELS, default=2)
    target_percent = models.PositiveSmallIntegerField(default=60, help_text="Score a student needs to attain this CO")
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "code"]

    def __str__(self):
        return self.code


class Unit(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="units")
    number = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=250)
    hours = models.PositiveSmallIntegerField(default=0)
    outcomes = models.ManyToManyField(CourseOutcome, blank=True, related_name="units")

    class Meta:
        ordering = ["number"]

    def __str__(self):
        return f"Unit {self.number}: {self.title}"


class Topic(models.Model):
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name="topics")
    title = models.CharField(max_length=300)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "pk"]

    def __str__(self):
        return self.title


class Reference(models.Model):
    TEXTBOOK, REFERENCE, ONLINE = "textbook", "reference", "online"
    KIND_CHOICES = [(TEXTBOOK, "Textbook"), (REFERENCE, "Reference book"), (ONLINE, "Online resource")]
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="references")
    kind = models.CharField(max_length=12, choices=KIND_CHOICES, default=TEXTBOOK)
    text = models.TextField()

    def __str__(self):
        return self.text[:80]


class Experiment(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="experiments")
    number = models.PositiveSmallIntegerField()
    title = models.TextField()

    class Meta:
        ordering = ["number"]

    def __str__(self):
        return f"Exp {self.number}: {self.title[:60]}"


class Enrollment(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="enrollments")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="enrollments")
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("course", "student")]


class PlanItem(models.Model):
    """One planned contact session in the learning plan (lesson plan)."""

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="plan_items")
    sequence = models.PositiveIntegerField()
    session_type = models.CharField(max_length=10, choices=SESSION_TYPES, default="lecture")
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, blank=True, related_name="plan_items")
    topics = models.ManyToManyField(Topic, blank=True, related_name="plan_items")
    experiment = models.ForeignKey(Experiment, on_delete=models.SET_NULL, null=True, blank=True)
    title = models.CharField(max_length=400)
    objective = models.TextField("Learning objective", blank=True)
    bloom_level = models.PositiveSmallIntegerField(choices=BLOOM_LEVELS, default=2)
    outcomes = models.ManyToManyField(CourseOutcome, blank=True, related_name="plan_items")
    duration_minutes = models.PositiveSmallIntegerField(default=60)
    pedagogy = models.CharField(max_length=200, blank=True)
    tools = models.TextField("Digital tools", blank=True)
    pre_class = models.TextField("Before class", blank=True)
    in_class = models.TextField("In class", blank=True)
    post_class = models.TextField("After class", blank=True)
    resources = models.TextField(blank=True)

    class Meta:
        ordering = ["sequence"]

    def __str__(self):
        return f"#{self.sequence} {self.title}"

    @property
    def co_codes(self):
        return ", ".join(o.code for o in self.outcomes.all())


class Rubric(models.Model):
    KIND_CHOICES = [
        ("assignment", "Assignment"),
        ("lab", "Lab / practical"),
        ("project", "Project"),
        ("presentation", "Presentation / seminar"),
        ("custom", "Custom"),
    ]
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="rubrics")
    title = models.CharField(max_length=200)
    kind = models.CharField(max_length=15, choices=KIND_CHOICES, default="custom")
    description = models.TextField(blank=True)
    is_template = models.BooleanField(default=True, help_text="Shown in the course rubric library")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_template", "title"]

    def __str__(self):
        return self.title

    @property
    def total_points(self):
        return sum((c.max_points for c in self.criteria.all()), Decimal("0"))

    def clone(self, title=None, outcomes=None, is_template=False):
        """Copy this rubric. If outcomes are given, criteria are mapped to them round-robin."""
        new = Rubric.objects.create(
            course=self.course, title=title or self.title, kind=self.kind,
            description=self.description, is_template=is_template,
        )
        outcomes = list(outcomes or [])
        for i, crit in enumerate(self.criteria.all()):
            outcome = outcomes[i % len(outcomes)] if outcomes else crit.outcome
            new_crit = RubricCriterion.objects.create(
                rubric=new, order=crit.order, title=crit.title, description=crit.description,
                max_points=crit.max_points, outcome=outcome,
            )
            for lvl in crit.levels.all():
                RubricLevel.objects.create(
                    criterion=new_crit, order=lvl.order, label=lvl.label, points=lvl.points, descriptor=lvl.descriptor
                )
        return new


class RubricCriterion(models.Model):
    rubric = models.ForeignKey(Rubric, on_delete=models.CASCADE, related_name="criteria")
    order = models.PositiveSmallIntegerField(default=0)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    max_points = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("5"))
    outcome = models.ForeignKey(CourseOutcome, on_delete=models.SET_NULL, null=True, blank=True, related_name="criteria")

    class Meta:
        ordering = ["order", "pk"]

    def __str__(self):
        return self.title


class RubricLevel(models.Model):
    criterion = models.ForeignKey(RubricCriterion, on_delete=models.CASCADE, related_name="levels")
    order = models.PositiveSmallIntegerField(default=0)
    label = models.CharField(max_length=60)
    points = models.DecimalField(max_digits=6, decimal_places=2)
    descriptor = models.TextField(blank=True)

    class Meta:
        ordering = ["order", "-points"]

    def __str__(self):
        return f"{self.label} ({self.points})"
