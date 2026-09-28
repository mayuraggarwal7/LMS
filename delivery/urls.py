from django.urls import path

from . import views

urlpatterns = [
    path("timetable/", views.timetable, name="timetable"),
    path("calendar.ics", views.calendar_ics, name="calendar_ics"),
    path("sessions/", views.session_list, name="session_list"),
    path("sessions/add/", views.session_add, name="session_add"),
    path("sessions/<int:session_id>/", views.session_detail, name="session_detail"),
    path("checkin/", views.student_checkin, name="student_checkin"),
    path("attendance/", views.attendance_report, name="attendance_report"),
]
