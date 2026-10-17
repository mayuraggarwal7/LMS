"""Academic term selection (like switching Odd/Even/Re-exam terms in a college ERP)."""

from django.db.models import Q

from .models import AcademicTerm

SESSION_KEY = "term_id"


def current_term(request):
    """The term the user switched to, else the institution's active term (None when no terms exist)."""
    if not hasattr(request, "_current_term"):
        term = None
        term_id = request.session.get(SESSION_KEY) if hasattr(request, "session") else None
        if term_id:
            term = AcademicTerm.objects.filter(pk=term_id).first()
        request._current_term = term or AcademicTerm.objects.filter(is_active=True).first()
    return request._current_term


def in_term(queryset, term, field="term"):
    """Filter courses (or anything with a course/term path) to a term; untermed items always show."""
    if term is None:
        return queryset
    return queryset.filter(Q(**{field: term}) | Q(**{f"{field}__isnull": True}))
