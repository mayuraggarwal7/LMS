from django.urls import path

from . import views

urlpatterns = [
    path("", views.exam_list, name="exam_list"),
    path("scheme/", views.scheme, name="exam_scheme"),
    path("new/", views.exam_new, name="exam_new"),
    path("retake/new/", views.retake_new, name="retake_new"),
    path("questions/", views.question_bank, name="question_bank"),
    path("questions/<int:question_id>/", views.question_edit, name="question_edit"),
    path("attainment/", views.co_attainment, name="co_attainment"),
    path("<int:exam_id>/", views.exam_detail, name="exam_detail"),
    path("<int:exam_id>/print/", views.exam_print, name="exam_print"),
    path("<int:exam_id>/marks/", views.exam_marks, name="exam_marks"),
]
