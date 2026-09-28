from .ai import ai_available


def lms(request):
    return {"ai_enabled": ai_available()}
