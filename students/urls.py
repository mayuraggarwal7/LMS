from django.urls import path

from . import views

urlpatterns = [
    path("", views.student_list, name="student_list"),
    path("admit/", views.admit, name="admit_student"),
    path("me/", views.my_progress, name="my_progress"),
    path("promote/", views.bulk_promote, name="bulk_promote"),
    path("<int:student_id>/", views.student_detail, name="student_detail"),
    path("<int:student_id>/compute/", views.compute, name="compute_semester"),
    path("<int:student_id>/promote/", views.promote_view, name="promote_student"),
]
