from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="finance_dashboard"),
    path("students/<int:student_id>/", views.student_account, name="student_account"),
    path("payroll/", views.payroll, name="payroll"),
    path("payroll/<int:run_id>/", views.payroll_run, name="payroll_run"),
]
