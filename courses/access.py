from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from .models import Course


def course_member(view):
    """Inject `course` for teachers and enrolled students; sets request.is_course_teacher."""

    @login_required
    @wraps(view)
    def wrapper(request, course_id, *args, **kwargs):
        course = get_object_or_404(Course, pk=course_id)
        request.is_course_teacher = course.is_teacher(request.user)
        if not request.is_course_teacher and not course.enrollments.filter(student=request.user).exists():
            raise PermissionDenied
        return view(request, course, *args, **kwargs)

    return wrapper


def course_teacher(view):
    @login_required
    @wraps(view)
    def wrapper(request, course_id, *args, **kwargs):
        course = get_object_or_404(Course, pk=course_id)
        if not course.is_teacher(request.user):
            raise PermissionDenied
        request.is_course_teacher = True
        return view(request, course, *args, **kwargs)

    return wrapper


def teacher_required(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_teacher:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper
