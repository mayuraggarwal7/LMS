"""Biometric attendance devices and punches for students and staff."""

import secrets

from django.conf import settings
from django.db import models


def generate_api_key():
    return secrets.token_urlsafe(24)


class Device(models.Model):
    name = models.CharField(max_length=80)
    location = models.CharField(max_length=120, blank=True, help_text="e.g. Main gate, Computer dept lab block")
    serial_no = models.CharField(max_length=60, blank=True)
    vendor = models.CharField(max_length=60, blank=True, help_text="e.g. ZKTeco, eSSL, Matrix")
    api_key = models.CharField(max_length=60, unique=True, default=generate_api_key)
    active = models.BooleanField(default=True)
    last_seen = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} ({self.location})" if self.location else self.name


class Punch(models.Model):
    IN, OUT, AUTO = "in", "out", "auto"
    DIRECTIONS = [(IN, "In"), (OUT, "Out"), (AUTO, "Unknown (device decides by order)")]
    device = models.ForeignKey(Device, on_delete=models.SET_NULL, null=True, blank=True, related_name="punches")
    person = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="punches")
    biometric_id = models.CharField(max_length=40, help_text="Enrolment id as sent by the device")
    timestamp = models.DateTimeField(db_index=True)
    direction = models.CharField(max_length=4, choices=DIRECTIONS, default=AUTO)
    source = models.CharField(max_length=10, default="api", help_text="api / csv / manual")

    class Meta:
        ordering = ["timestamp"]
        unique_together = [("biometric_id", "timestamp", "device")]

    def __str__(self):
        return f"{self.biometric_id} @ {self.timestamp:%Y-%m-%d %H:%M}"
