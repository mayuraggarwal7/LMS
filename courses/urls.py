from django.urls import path

from . import views

urlpatterns = [
    path("new/", views.course_new, name="course_new"),
    path("new/review/", views.course_review, name="course_review"),
    path("join/", views.join_course, name="join_course"),
    path("terms/", views.term_switch, name="term_switch"),
    path("<int:course_id>/", views.course_detail, name="course_detail"),
    path("<int:course_id>/setup-summary/", views.course_setup_summary, name="course_setup_summary"),
    path("<int:course_id>/settings/", views.course_settings, name="course_settings"),
    path("<int:course_id>/delete/", views.course_delete, name="course_delete"),
    path("<int:course_id>/syllabus/", views.course_syllabus, name="course_syllabus"),
    path("<int:course_id>/plan/", views.course_plan, name="course_plan"),
    path("<int:course_id>/plan/add/", views.plan_item_edit, name="plan_item_add"),
    path("<int:course_id>/plan/regenerate/", views.plan_regenerate, name="plan_regenerate"),
    path("<int:course_id>/plan/import/", views.plan_import, name="plan_import"),
    path("<int:course_id>/lesson-plan/", views.lesson_plan_document, name="lesson_plan_document"),
    path("<int:course_id>/plan/<int:item_id>/", views.plan_item_edit, name="plan_item_edit"),
    path("<int:course_id>/plan/<int:item_id>/move/", views.plan_item_move, name="plan_item_move"),
    path("<int:course_id>/plan/<int:item_id>/delete/", views.plan_item_delete, name="plan_item_delete"),
    path("<int:course_id>/rubrics/", views.rubric_list, name="rubric_list"),
    path("<int:course_id>/rubrics/new/", views.rubric_create, name="rubric_create"),
    path("<int:course_id>/rubrics/<int:rubric_id>/", views.rubric_edit, name="rubric_edit"),
    path("<int:course_id>/people/", views.course_people, name="course_people"),
    path("<int:course_id>/people/<int:user_id>/remove/", views.remove_student, name="remove_student"),
]
