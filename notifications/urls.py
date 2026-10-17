from django.urls import path

from . import views

urlpatterns = [
    path("", views.center, name="notification_center"),
    path("count.json", views.count, name="notification_count"),
    path("<int:notification_id>/go/", views.go, name="notification_go"),
    path("deadlines/", views.deadlines, name="deadlines"),
    path("deadlines/<int:deadline_id>/", views.deadline_detail, name="deadline_detail"),
]
