from django.contrib import admin

from .models import Competency, FacultyCompetency, FacultyProfile, FacultyRating, WorkloadAllocation


class CompetencyInline(admin.TabularInline):
    model = FacultyCompetency
    extra = 0


class AllocationInline(admin.TabularInline):
    model = WorkloadAllocation
    extra = 0


@admin.register(FacultyProfile)
class FacultyProfileAdmin(admin.ModelAdmin):
    list_display = ["employee_id", "user", "department", "designation", "staff_type", "employment", "has_phd", "active"]
    list_filter = ["department", "designation", "staff_type", "employment", "has_phd"]
    search_fields = ["employee_id", "user__first_name", "user__last_name"]
    inlines = [CompetencyInline, AllocationInline]


admin.site.register([Competency, FacultyRating, WorkloadAllocation])
