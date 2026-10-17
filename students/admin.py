from django.contrib import admin

from .models import CourseResult, SemesterRecord, StudentProfile


class SemesterInline(admin.TabularInline):
    model = SemesterRecord
    extra = 0


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = ["admission_no", "user", "program", "admission_year", "admission_type", "category", "current_semester", "status"]
    list_filter = ["program", "admission_year", "admission_type", "category", "status"]
    search_fields = ["admission_no", "user__first_name", "user__last_name", "user__roll_no", "user__prn"]
    inlines = [SemesterInline]


admin.site.register([SemesterRecord, CourseResult])
