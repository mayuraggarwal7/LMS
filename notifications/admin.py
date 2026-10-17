from django.contrib import admin

from .models import Deadline, DeadlineAck, Notification


@admin.register(Deadline)
class DeadlineAdmin(admin.ModelAdmin):
    list_display = ["title", "kind", "component_kind", "due_at", "audience", "department", "year", "active"]
    list_filter = ["kind", "audience", "active"]


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["user", "title", "level", "category", "updated_at", "read_at", "resolved_at"]
    list_filter = ["level", "category"]
    search_fields = ["title", "user__username"]


admin.site.register(DeadlineAck)
