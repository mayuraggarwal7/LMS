from django.contrib import admin

from .models import Device, Punch


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ["name", "location", "vendor", "serial_no", "active", "last_seen"]
    readonly_fields = ["api_key"]


@admin.register(Punch)
class PunchAdmin(admin.ModelAdmin):
    list_display = ["biometric_id", "person", "timestamp", "direction", "device", "source"]
    list_filter = ["device", "source", "direction"]
    search_fields = ["biometric_id", "person__first_name", "person__last_name"]
    date_hierarchy = "timestamp"
