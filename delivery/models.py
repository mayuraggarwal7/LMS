import random

from django.conf import settings
from django.db import models

from courses.models import SESSION_TYPES, Course, PlanItem

WEEKDAYS = [(0, "Monday"), (1, "Tuesday"), (2, "Wednesday"), (3, "Thursday"), (4, "Friday"), (5, "Saturday"), (6, "Sunday")]


class TimetableSlot(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="slots")
    weekday = models.PositiveSmallIntegerField(choices=WEEKDAYS)
    start_time = models.TimeField()
    end_time = models.TimeField()
    session_type = models.CharField(max_length=10, choices=SESSION_TYPES, default="lecture")
    room = models.CharField(max_length=60, blank=True)
    batch = models.CharField(max_length=30, blank=True, help_text="Lab batch, e.g. B1")

    class Meta:
        ordering = ["weekday", "start_time"]

    def __str__(self):
        return f"{self.get_weekday_display()} {self.start_time:%H:%M}-{self.end_time:%H:%M} {self.get_session_type_display()}"


class Holiday(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="holidays")
    date = models.DateField()
    name = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["date"]
        unique_together = [("course", "date")]

    def __str__(self):
        return f"{self.date} {self.name}"


class SupplementaryDay(models.Model):
    """An extra teaching day that follows another weekday's timetable (e.g. Saturday runs Monday's classes)."""

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="supplementary_days")
    date = models.DateField()
    follows_weekday = models.PositiveSmallIntegerField("Follow the timetable of", choices=WEEKDAYS)
    reason = models.CharField(max_length=120, blank=True, help_text="e.g. compensation for the 15 Aug holiday")

    class Meta:
        ordering = ["date"]
        unique_together = [("course", "date")]

    def __str__(self):
        return f"{self.date} follows {self.get_follows_weekday_display()}"


def generate_checkin_code():
    return f"{random.randint(0, 999999):06d}"


class ClassSession(models.Model):
    SCHEDULED, COMPLETED, CANCELLED = "scheduled", "completed", "cancelled"
    STATUS_CHOICES = [(SCHEDULED, "Scheduled"), (COMPLETED, "Delivered"), (CANCELLED, "Cancelled")]
    COVERAGE_CHOICES = [("full", "Fully covered"), ("partial", "Partially covered"), ("different", "Covered something else")]

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="sessions")
    slot = models.ForeignKey(TimetableSlot, on_delete=models.SET_NULL, null=True, blank=True, related_name="sessions")
    plan_item = models.ForeignKey(PlanItem, on_delete=models.SET_NULL, null=True, blank=True, related_name="sessions")
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    session_type = models.CharField(max_length=10, choices=SESSION_TYPES, default="lecture")
    room = models.CharField(max_length=60, blank=True)
    is_extra = models.BooleanField(default=False, help_text="Added manually (not generated from the timetable)")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=SCHEDULED)
    coverage = models.CharField(max_length=10, choices=COVERAGE_CHOICES, blank=True)
    actual_topics = models.TextField("What was actually covered", blank=True)
    notes = models.TextField("Class notes", blank=True)
    notes_file = models.FileField(upload_to="class_notes/", blank=True)
    resource_link = models.URLField("Slides / recording link", blank=True)
    notes_shared = models.BooleanField("Share notes with students", default=True)
    checkin_code = models.CharField(max_length=6, default=generate_checkin_code)
    checkin_open = models.BooleanField(default=False)
    plan_pinned = models.BooleanField(default=False, help_text="Plan item chosen by hand; timetable sync keeps it")
    cancel_reason = models.CharField(max_length=160, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["date", "start_time"]

    def __str__(self):
        return f"{self.course.code} {self.date} {self.start_time:%H:%M}"

    @property
    def is_locked(self):
        """A session that sync must never delete (delivered, cancelled, added by hand, or already has notes/attendance)."""
        return self.status != self.SCHEDULED or self.is_extra or bool(self.notes) or self.attendance.exists()

    @property
    def keeps_plan_item(self):
        """A scheduled session whose plan item sync must not change."""
        return self.plan_pinned or bool(self.notes) or self.attendance.exists()


class AttendanceRecord(models.Model):
    PRESENT, ABSENT, LATE, EXCUSED = "P", "A", "L", "E"
    STATUS_CHOICES = [(PRESENT, "Present"), (ABSENT, "Absent"), (LATE, "Late"), (EXCUSED, "Excused / OD")]
    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="attendance")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="attendance")
    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default=PRESENT)
    self_checked_in = models.BooleanField(default=False)
    marked_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("session", "student")]

    @property
    def counts_present(self):
        return self.status in (self.PRESENT, self.LATE, self.EXCUSED)
