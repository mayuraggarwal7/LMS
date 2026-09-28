from django.contrib.auth.models import AbstractUser
from django.db import models

YEAR_CHOICES = [(1, "First year"), (2, "Second year"), (3, "Third year"), (4, "Final year")]


class User(AbstractUser):
    TEACHER = "teacher"
    STUDENT = "student"
    ROLE_CHOICES = [(TEACHER, "Teacher"), (STUDENT, "Student")]

    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=STUDENT)
    department = models.CharField(max_length=120, blank=True)
    year_of_study = models.PositiveSmallIntegerField(choices=YEAR_CHOICES, null=True, blank=True)
    roll_no = models.CharField("Roll / PRN number", max_length=40, blank=True)

    class Meta:
        ordering = ["roll_no", "first_name", "last_name", "username"]

    @property
    def is_teacher(self):
        return self.role == self.TEACHER or self.is_superuser

    @property
    def is_student(self):
        return self.role == self.STUDENT

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    def __str__(self):
        label = self.display_name
        return f"{self.roll_no} · {label}" if self.roll_no else label
