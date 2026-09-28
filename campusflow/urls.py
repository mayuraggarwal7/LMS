from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("courses/", include("courses.urls")),
    path("attendance/", include("delivery.urls_global")),
    path("courses/<int:course_id>/delivery/", include("delivery.urls")),
    path("courses/<int:course_id>/classroom/", include("classroom.urls")),
    path("courses/<int:course_id>/exams/", include("exams.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif settings.LMS_SERVE_MEDIA:
    urlpatterns += [re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT})]
