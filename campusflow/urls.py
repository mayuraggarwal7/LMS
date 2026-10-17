from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

from biometric import views as biometric_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("courses/", include("courses.urls")),
    path("attendance/", include("delivery.urls_global")),
    path("admin-console/", include("institution.urls")),
    path("students/", include("students.urls")),
    path("hr/", include("hr.urls")),
    path("finance/", include("finance.urls")),
    path("notifications/", include("notifications.urls")),
    path("biometric/", include("biometric.urls")),
    path("api/biometric/punches", biometric_views.api_punches, name="api_punches"),
    path("courses/<int:course_id>/biometric/", biometric_views.course_report, name="course_biometric"),
    path("courses/<int:course_id>/delivery/", include("delivery.urls")),
    path("courses/<int:course_id>/classroom/", include("classroom.urls")),
    path("courses/<int:course_id>/exams/", include("exams.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif settings.LMS_SERVE_MEDIA:
    urlpatterns += [re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT})]
