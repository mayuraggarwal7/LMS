from .ai import ai_available
from .terms import current_term


def lms(request):
    ctx = {"ai_enabled": ai_available()}
    if getattr(request, "user", None) is not None and request.user.is_authenticated:
        ctx["current_term"] = current_term(request)
    return ctx
