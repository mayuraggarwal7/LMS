from django.urls import path

from . import views

urlpatterns = [
    path("pending/", views.pending_attendance, name="pending_attendance"),
    path("schedule/", views.schedule_changes, name="schedule_changes"),
]
