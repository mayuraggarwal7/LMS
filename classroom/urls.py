from django.urls import path

from . import views

urlpatterns = [
    path("stream/", views.stream, name="stream"),
    path("classwork/", views.classwork, name="classwork"),
    path("assignments/new/", views.assignment_edit, name="assignment_new"),
    path("assignments/<int:assignment_id>/", views.assignment_detail, name="assignment_detail"),
    path("assignments/<int:assignment_id>/edit/", views.assignment_edit, name="assignment_edit"),
    path("assignments/<int:assignment_id>/status/", views.assignment_status, name="assignment_status"),
    path("assignments/<int:assignment_id>/import/", views.import_grades, name="import_grades"),
    path("assignments/<int:assignment_id>/return-all/", views.return_all, name="return_all"),
    path("assignments/<int:assignment_id>/grade/<int:student_id>/", views.grade_submission, name="grade_submission"),
    path("gradebook/", views.gradebook, name="gradebook"),
    path("cie-report/", views.cie_report, name="cie_report"),
    path("my-grades/", views.my_grades, name="my_grades"),
]
