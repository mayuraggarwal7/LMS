from .services import maybe_refresh, unread_count


def notifications(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not hasattr(request, "session"):
        return {}
    try:
        maybe_refresh(request)
        return {"notif_unread": unread_count(user)}
    except Exception:  # never let the badge break a page
        return {"notif_unread": 0}
