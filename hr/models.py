"""Teacher HR: profiles (teaching and lab staff), competencies, workload allocation, ratings."""

from decimal import Decimal

from django.conf import settings
from django.db import models

from institution.models import Department


class FacultyProfile(models.Model):
    TEACHING, LAB, NON_TEACHING = "teaching", "lab", "nonteaching"
    STAFF_TYPES = [(TEACHING, "Teaching faculty"), (LAB, "Lab / technical staff"), (NON_TEACHING, "Non-teaching staff")]
    DESIGNATIONS = [
        ("principal", "Principal"), ("professor", "Professor"), ("associate", "Associate Professor"),
        ("assistant", "Assistant Professor"), ("adjunct", "Adjunct / Visiting faculty"),
        ("lab_assistant", "Lab Assistant"), ("lab_technician", "Lab Technician"), ("instructor", "Workshop Instructor"),
        ("admin", "Administrative staff"),
    ]
    PERMANENT, CONTRACT, VISITING = "permanent", "contract", "visiting"
    EMPLOYMENT = [(PERMANENT, "Permanent"), (CONTRACT, "Contract / ad hoc"), (VISITING, "Visiting (paid per hour)")]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="faculty_profile")
    employee_id = models.CharField(max_length=30, unique=True)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="staff")
    staff_type = models.CharField(max_length=12, choices=STAFF_TYPES, default=TEACHING)
    designation = models.CharField(max_length=20, choices=DESIGNATIONS, default="assistant")
    employment = models.CharField(max_length=10, choices=EMPLOYMENT, default=PERMANENT)
    qualification = models.CharField(max_length=120, blank=True)
    has_phd = models.BooleanField(default=False)
    specialization = models.TextField(blank=True)
    experience_years = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    date_of_joining = models.DateField(null=True, blank=True)
    biometric_id = models.CharField(max_length=30, blank=True, help_text="Enrolment number on the biometric device")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["department__name", "designation", "user__first_name"]

    def __str__(self):
        return f"{self.user.display_name} ({self.get_designation_display()})"

    @property
    def workload_rule_key(self):
        return {"professor": "workload_professor", "principal": "workload_professor", "associate": "workload_associate",
                "assistant": "workload_assistant", "adjunct": "workload_assistant"}.get(
            self.designation, "workload_lab_staff")


class Competency(models.Model):
    SUBJECT, PEDAGOGY, RESEARCH, LAB, ADMIN = "subject", "pedagogy", "research", "lab", "admin"
    AREAS = [(SUBJECT, "Subject expertise"), (PEDAGOGY, "Teaching & pedagogy"), (RESEARCH, "Research"),
             (LAB, "Lab / tools"), (ADMIN, "Administration")]
    name = models.CharField(max_length=120, unique=True)
    area = models.CharField(max_length=10, choices=AREAS, default=SUBJECT)

    class Meta:
        ordering = ["area", "name"]
        verbose_name_plural = "competencies"

    def __str__(self):
        return self.name


class FacultyCompetency(models.Model):
    LEVELS = [(1, "1 Aware"), (2, "2 Working knowledge"), (3, "3 Proficient"), (4, "4 Advanced"), (5, "5 Expert")]
    faculty = models.ForeignKey(FacultyProfile, on_delete=models.CASCADE, related_name="competencies")
    competency = models.ForeignKey(Competency, on_delete=models.CASCADE, related_name="holders")
    level = models.PositiveSmallIntegerField(choices=LEVELS, default=3)
    evidence = models.CharField(max_length=200, blank=True, help_text="Courses taught, certification, publications…")

    class Meta:
        unique_together = [("faculty", "competency")]
        ordering = ["-level"]


class WorkloadAllocation(models.Model):
    THEORY, TUTORIAL, LAB, LAB_ASSIST, PROJECT, ADMIN = "theory", "tutorial", "lab", "labassist", "project", "admin"
    ROLES = [(THEORY, "Theory lectures"), (TUTORIAL, "Tutorials"), (LAB, "Lab / practicals (teacher)"),
             (LAB_ASSIST, "Lab assistance (lab staff)"), (PROJECT, "Project guidance"), (ADMIN, "Administrative duty")]
    faculty = models.ForeignKey(FacultyProfile, on_delete=models.CASCADE, related_name="allocations")
    term = models.ForeignKey("courses.AcademicTerm", on_delete=models.SET_NULL, null=True, blank=True)
    course = models.ForeignKey("courses.Course", on_delete=models.SET_NULL, null=True, blank=True, related_name="allocations")
    description = models.CharField(max_length=200, blank=True, help_text="When not tied to a course, e.g. 'Exam cell'")
    role = models.CharField(max_length=10, choices=ROLES, default=THEORY)
    hours_per_week = models.DecimalField(max_digits=4, decimal_places=1)
    division = models.CharField(max_length=10, blank=True)

    class Meta:
        ordering = ["faculty", "role"]

    def __str__(self):
        return f"{self.faculty}: {self.course or self.description} ({self.hours_per_week} h)"

    @property
    def counts_as_teaching(self):
        return self.role in (self.THEORY, self.TUTORIAL, self.LAB)


class FacultyRating(models.Model):
    SOURCES = [("students", "Student feedback"), ("hod", "HOD review"), ("peer", "Peer observation"),
               ("self", "Self appraisal"), ("principal", "Principal / appraisal committee")]
    CRITERIA = [("teaching", "Teaching effectiveness"), ("punctuality", "Punctuality & regularity"),
                ("research", "Research & publications"), ("admin", "Administrative contribution"),
                ("overall", "Overall")]
    faculty = models.ForeignKey(FacultyProfile, on_delete=models.CASCADE, related_name="ratings")
    term = models.ForeignKey("courses.AcademicTerm", on_delete=models.SET_NULL, null=True, blank=True)
    course = models.ForeignKey("courses.Course", on_delete=models.SET_NULL, null=True, blank=True)
    source = models.CharField(max_length=10, choices=SOURCES, default="students")
    criterion = models.CharField(max_length=12, choices=CRITERIA, default="overall")
    score = models.DecimalField(max_digits=3, decimal_places=2, help_text="1 to 5")
    responses = models.PositiveIntegerField(default=1, help_text="Number of feedback responses averaged")
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def clean(self):
        from django.core.exceptions import ValidationError

        if not (Decimal("1") <= self.score <= Decimal("5")):
            raise ValidationError({"score": "Score must be between 1 and 5."})
