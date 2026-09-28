from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from courses.models import Course, CourseOutcome, Rubric, RubricCriterion, RubricLevel, Unit


class Announcement(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="announcements")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Assignment(models.Model):
    KIND_CHOICES = [
        ("assignment", "Assignment"),
        ("lab", "Lab write-up / journal"),
        ("quiz", "Quiz / class test"),
        ("project", "Mini project"),
        ("presentation", "Presentation / seminar"),
        ("activity", "Activity"),
    ]
    DRAFT, PUBLISHED = "draft", "published"
    STATUS_CHOICES = [(DRAFT, "Draft"), (PUBLISHED, "Published")]

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="assignments")
    title = models.CharField(max_length=250)
    instructions = models.TextField(blank=True)
    kind = models.CharField(max_length=15, choices=KIND_CHOICES, default="assignment")
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, blank=True)
    outcomes = models.ManyToManyField(CourseOutcome, blank=True)
    component = models.ForeignKey(
        "exams.AssessmentComponent", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="assignments", help_text="Which ISE / term-work bucket this counts toward",
    )
    rubric = models.ForeignKey(Rubric, on_delete=models.SET_NULL, null=True, blank=True, related_name="assignments")
    max_points = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("10"))
    due_at = models.DateTimeField(null=True, blank=True)
    attachment = models.FileField(upload_to="assignments/", blank=True)
    reference_link = models.URLField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["due_at", "created_at"]

    def __str__(self):
        return self.title

    @property
    def is_published(self):
        return self.status == self.PUBLISHED

    @property
    def is_overdue(self):
        return bool(self.due_at and timezone.now() > self.due_at)


class Submission(models.Model):
    ASSIGNED, TURNED_IN, RETURNED = "assigned", "turned_in", "returned"
    STATUS_CHOICES = [(ASSIGNED, "Assigned"), (TURNED_IN, "Turned in"), (RETURNED, "Graded & returned")]

    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="submissions")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="submissions")
    text = models.TextField("Answer / notes", blank=True)
    file = models.FileField(upload_to="submissions/", blank=True)
    link = models.URLField("Link (GitHub, Colab, Drive...)", blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=ASSIGNED)
    submitted_at = models.DateTimeField(null=True, blank=True)
    score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    feedback = models.TextField(blank=True)
    graded_at = models.DateTimeField(null=True, blank=True)
    score_source = models.CharField(
        max_length=10, blank=True, help_text="'rubric' when computed from criteria, 'total' when entered directly"
    )

    class Meta:
        unique_together = [("assignment", "student")]

    @property
    def is_late(self):
        a = self.assignment
        return bool(self.submitted_at and a.due_at and self.submitted_at > a.due_at)

    @property
    def percent(self):
        if self.score is None or not self.assignment.max_points:
            return None
        return float(self.score) / float(self.assignment.max_points) * 100


class CriterionScore(models.Model):
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="criterion_scores")
    criterion = models.ForeignKey(RubricCriterion, on_delete=models.CASCADE)
    level = models.ForeignKey(RubricLevel, on_delete=models.SET_NULL, null=True, blank=True)
    points = models.DecimalField(max_digits=6, decimal_places=2)
    comment = models.CharField(max_length=300, blank=True)
    derived = models.BooleanField(default=False, help_text="Distributed from a total score, not picked directly")

    class Meta:
        unique_together = [("submission", "criterion")]
