from django.contrib import admin

from .models import AttendanceRecord, ClassSession, Holiday, TimetableSlot


@admin.register(ClassSession)
class ClassSessionAdmin(admin.ModelAdmin):
    list_display = ["course", "date", "start_time", "session_type", "status", "plan_item"]
    list_filter = ["status", "session_type", "course"]


admin.site.register([TimetableSlot, Holiday, AttendanceRecord])
