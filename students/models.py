"""Student lifecycle: admission -> semester by semester -> passing out."""

from decimal import Decimal

from django.conf import settings
from django.db import models

from institution.models import Program


class StudentProfile(models.Model):
    CAP, INSTITUTE, MINORITY, LATERAL, TFWS, NRI = "cap", "institute", "minority", "lateral", "tfws", "nri"
    ADMISSION_TYPES = [
        (CAP, "Centralised admission (CAP)"), (INSTITUTE, "Institute level"), (MINORITY, "Minority quota"),
        (LATERAL, "Lateral entry (Direct Second Year)"), (TFWS, "Tuition Fee Waiver Scheme"), (NRI, "NRI / OCI"),
    ]
    CATEGORIES = [(c, c) for c in ("Open", "OBC", "SC", "ST", "VJ/DT", "NT", "SBC", "EWS", "SEBC", "Other")]
    ACTIVE, DETAINED, YEAR_DROP, PASSED_OUT, LEFT = "active", "detained", "yeardrop", "passed", "left"
    STATUSES = [(ACTIVE, "Studying"), (DETAINED, "Detained"), (YEAR_DROP, "Year drop"),
                (PASSED_OUT, "Passed out"), (LEFT, "Left / cancelled")]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="student_profile")
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="students")
    admission_no = models.CharField(max_length=40, unique=True)
    admission_year = models.PositiveSmallIntegerField()
    admission_type = models.CharField(max_length=10, choices=ADMISSION_TYPES, default=CAP)
    category = models.CharField(max_length=10, choices=CATEGORIES, default="Open")
    entry_semester = models.PositiveSmallIntegerField(default=1, help_text="3 for lateral entry")
    current_semester = models.PositiveSmallIntegerField(default=1)
    division = models.CharField(max_length=10, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=ACTIVE)
    qualifying_exam = models.CharField(max_length=80, blank=True, help_text="e.g. MHT-CET / JEE Main / Diploma")
    qualifying_score = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    passed_out_year = models.PositiveSmallIntegerField(null=True, blank=True)
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["program__code", "admission_year", "user__roll_no"]

    def __str__(self):
        return f"{self.user.display_name} ({self.admission_no})"

    @property
    def year_of_study(self):
        return (self.current_semester + 1) // 2

    @property
    def expected_passing_year(self):
        remaining_semesters = self.program.semesters - self.entry_semester + 1
        return self.admission_year + (remaining_semesters + 1) // 2

    def cgpa(self):
        records = [r for r in self.semesters.all() if r.sgpa is not None and r.credits_earned]
        credits = sum((r.credits_earned for r in records), Decimal("0"))
        if not credits:
            return None
        return (sum((r.sgpa * r.credits_earned for r in records), Decimal("0")) / credits).quantize(Decimal("0.01"))

    def total_credits(self):
        return sum((r.credits_earned or 0 for r in self.semesters.all()), Decimal("0"))

    def backlogs(self):
        latest = self.semesters.order_by("-semester").first()
        return latest.backlogs if latest else 0


class SemesterRecord(models.Model):
    PASS, ATKT, FAIL, DETAINED, IN_PROGRESS = "pass", "atkt", "fail", "detained", "ongoing"
    RESULTS = [(PASS, "Passed"), (ATKT, "Allowed to keep terms (ATKT)"), (FAIL, "Failed"),
               (DETAINED, "Detained (attendance)"), (IN_PROGRESS, "In progress")]

    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name="semesters")
    semester = models.PositiveSmallIntegerField()
    academic_year = models.CharField(max_length=10, blank=True, help_text="e.g. 2025-26")
    term = models.ForeignKey("courses.AcademicTerm", on_delete=models.SET_NULL, null=True, blank=True)
    sgpa = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    credits_attempted = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    credits_earned = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    attendance_percent = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    backlogs = models.PositiveSmallIntegerField("Courses not yet cleared (cumulative)", default=0)
    result = models.CharField(max_length=10, choices=RESULTS, default=IN_PROGRESS)
    source = models.CharField(max_length=20, default="manual", help_text="'lms' when computed from course grades")
    remarks = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["semester"]
        unique_together = [("student", "semester")]

    def __str__(self):
        return f"{self.student} sem {self.semester}"


class CourseResult(models.Model):
    """One course outcome inside a semester record (from the LMS gradebook or entered from the result sheet)."""

    record = models.ForeignKey(SemesterRecord, on_delete=models.CASCADE, related_name="courses")
    course = models.ForeignKey("courses.Course", on_delete=models.SET_NULL, null=True, blank=True)
    course_code = models.CharField(max_length=30)
    course_title = models.CharField(max_length=200)
    credits = models.DecimalField(max_digits=4, decimal_places=1)
    grade = models.CharField(max_length=4)
    grade_point = models.PositiveSmallIntegerField()
    attendance_percent = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)

    class Meta:
        ordering = ["course_code"]

    @property
    def passed(self):
        return self.grade_point >= 4
