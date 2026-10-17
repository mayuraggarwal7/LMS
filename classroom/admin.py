from django.contrib import admin

from .models import Announcement, Assignment, CriterionScore, Submission


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ["title", "course", "kind", "component", "max_points", "due_at", "status"]
    list_filter = ["status", "kind", "course"]


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ["assignment", "student", "status", "score", "submitted_at"]
    list_filter = ["status"]


admin.site.register([Announcement, CriterionScore])
