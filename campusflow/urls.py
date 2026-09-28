from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("courses/", include("courses.urls")),
    path("courses/<int:course_id>/delivery/", include("delivery.urls")),
    path("courses/<int:course_id>/classroom/", include("classroom.urls")),
    path("courses/<int:course_id>/exams/", include("exams.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
