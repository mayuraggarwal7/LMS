from django.db.models import Q

from .ai import ai_available
from .terms import current_term, in_term


def lms(request):
    ctx = {"ai_enabled": ai_available()}
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        term = current_term(request)
        ctx["current_term"] = term
        ctx["nav_courses"] = _nav_courses(user, term)
    return ctx


def _nav_courses(user, term):
    """The courses listed in the sidebar: taught (teachers) or enrolled (students) in the viewed term."""
    from .models import Course

    if user.is_teacher:
        qs = Course.objects.filter(Q(teacher=user) | Q(co_teachers=user))
    else:
        qs = Course.objects.filter(enrollments__student=user)
    return list(in_term(qs.filter(archived=False), term).distinct().order_by("code").only("pk", "code", "title")[:15])
