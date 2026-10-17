"""Notification centre: admin-set deadlines (a submission timetable) and each user's attention items."""

from django.conf import settings
from django.db import models
from django.utils import timezone


class Deadline(models.Model):
    """A dated task the administration sets for a group of users, e.g. 'Submit MSE papers by 12 Oct'.

    For most kinds the system checks completion itself from LMS data, per course; 'custom' and
    'notice' items are acknowledged by the user.
    """

    PAPER, MARKS, ATTENDANCE, PLAN, GRADING, CUSTOM, NOTICE = "paper", "marks", "attendance", "plan", "grading", "custom", "notice"
    KINDS = [
        (PAPER, "Submit exam paper"),
        (MARKS, "Enter & release marks"),
        (ATTENDANCE, "Attendance up to date"),
        (PLAN, "Learning plan & timetable ready"),
        (GRADING, "All submissions graded"),
        (CUSTOM, "Other task (user marks it done)"),
        (NOTICE, "Notice / announcement"),
    ]
    AUTO_KINDS = {PAPER, MARKS, ATTENDANCE, PLAN, GRADING}
    TEACHERS, STUDENTS, ALL_STAFF, EVERYONE = "teachers", "students", "staff", "everyone"
    AUDIENCES = [(TEACHERS, "Teachers of matching courses"), (STUDENTS, "Students"), (ALL_STAFF, "All staff"),
                 (EVERYONE, "Everyone")]
    COMPONENT_KINDS = [("", "Any"), ("ISE", "ISE"), ("MSE", "MSE"), ("ESE", "ESE"), ("TW", "Term work"), ("ORAL", "Oral / practical")]

    title = models.CharField(max_length=200)
    kind = models.CharField(max_length=12, choices=KINDS, default=PAPER)
    component_kind = models.CharField("Exam component", max_length=5, choices=COMPONENT_KINDS, blank=True,
                                      help_text="For paper / marks deadlines: which exam (MSE, ESE...)")
    due_at = models.DateTimeField(null=True, blank=True, help_text="Leave empty for a notice without a deadline")
    remind_days = models.PositiveSmallIntegerField("Remind from (days before)", default=7)
    audience = models.CharField(max_length=10, choices=AUDIENCES, default=TEACHERS)
    department = models.ForeignKey("institution.Department", on_delete=models.CASCADE, null=True, blank=True,
                                   help_text="Empty = all departments")
    program = models.ForeignKey("institution.Program", on_delete=models.CASCADE, null=True, blank=True)
    year = models.PositiveSmallIntegerField("Year of study", null=True, blank=True, help_text="Empty = all years")
    term = models.ForeignKey("courses.AcademicTerm", on_delete=models.SET_NULL, null=True, blank=True)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["due_at", "pk"]

    def __str__(self):
        return self.title

    @property
    def is_auto(self):
        return self.kind in self.AUTO_KINDS

    @property
    def overdue(self):
        return bool(self.due_at and timezone.now() > self.due_at)


class DeadlineAck(models.Model):
    """A user marking a custom deadline done (or a notice read)."""

    deadline = models.ForeignKey(Deadline, on_delete=models.CASCADE, related_name="acks")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    done_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        unique_together = [("deadline", "user")]


class Notification(models.Model):
    """One item in a user's notification centre. Attention items are kept in step with the data:
    they resolve themselves when the underlying work is done."""

    INFO, WARN, URGENT = "info", "warn", "urgent"
    LEVELS = [(INFO, "Update"), (WARN, "Due soon"), (URGENT, "Overdue")]
    CATEGORIES = [
        ("attendance", "Attendance"), ("grading", "Grading"), ("exam", "Exams"), ("deadline", "Deadlines"),
        ("classwork", "Classwork"), ("fees", "Fees"), ("progress", "Progress"), ("admin", "Administration"), ("notice", "Notices"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    key = models.CharField(max_length=120, help_text="Identifies the underlying item, so it is updated rather than duplicated")
    category = models.CharField(max_length=12, choices=CATEGORIES, default="notice")
    level = models.CharField(max_length=8, choices=LEVELS, default=INFO)
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=300, blank=True)
    url = models.CharField(max_length=300, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    actionable = models.BooleanField(default=True, help_text="Needs the user to do something")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    read_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    emailed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("user", "key")]
        ordering = ["resolved_at", "-updated_at"]
        indexes = [models.Index(fields=["user", "resolved_at", "read_at"])]

    def __str__(self):
        return f"{self.user}: {self.title}"

    @property
    def is_open(self):
        return self.resolved_at is None
