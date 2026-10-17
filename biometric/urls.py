from django.urls import path

from . import views

urlpatterns = [
    path("", views.overview, name="biometric"),
    path("devices/<int:device_id>/rotate/", views.device_rotate, name="device_rotate"),
]
