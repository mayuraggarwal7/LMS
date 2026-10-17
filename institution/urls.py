from django.urls import path

from . import views

urlpatterns = [
    path("", views.console, name="admin_console"),
    path("institution/", views.institution_edit, name="institution_edit"),
    path("departments/", views.departments, name="departments"),
    path("programs/new/", views.program_edit, name="program_new"),
    path("programs/<int:program_id>/", views.program_detail, name="program_detail"),
    path("programs/<int:program_id>/edit/", views.program_edit, name="program_edit"),
    path("rules/", views.rules, name="rules"),
    path("aicte/", views.aicte_report, name="aicte_report"),
]
