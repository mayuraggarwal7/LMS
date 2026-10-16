from django.urls import path

from . import views

urlpatterns = [
    path("", views.faculty_list, name="faculty_list"),
    path("new/", views.faculty_edit, name="faculty_new"),
    path("me/", views.my_hr, name="my_hr"),
    path("allocation/", views.allocation_board, name="allocation_board"),
    path("ratings/import/", views.rate_bulk, name="rate_bulk"),
    path("<int:faculty_id>/", views.faculty_detail, name="faculty_detail"),
    path("<int:faculty_id>/edit/", views.faculty_edit, name="faculty_edit"),
]
