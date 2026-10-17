"""Optional Claude-assisted features.

Everything in the LMS works without an API key: the heuristic parser and the
template-based generators are always available. When ANTHROPIC_API_KEY is set
(and the `anthropic` package is installed) these helpers are tried first and
their structured output is validated; any failure falls back silently.
"""

import json
import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)

SYLLABUS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "title", "credits", "lecture_hours", "tutorial_hours", "practical_hours",
                 "prerequisites", "outcomes", "units", "textbooks", "references", "experiments"],
    "properties": {
        "code": {"type": "string"},
        "title": {"type": "string"},
        "credits": {"type": "number"},
        "lecture_hours": {"type": "integer"},
        "tutorial_hours": {"type": "integer"},
        "practical_hours": {"type": "integer"},
        "prerequisites": {"type": "string"},
        "outcomes": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False, "required": ["code", "description"],
                "properties": {"code": {"type": "string"}, "description": {"type": "string"}},
            },
        },
        "units": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False, "required": ["number", "title", "hours", "topics"],
                "properties": {
                    "number": {"type": "integer"},
                    "title": {"type": "string"},
                    "hours": {"type": "integer"},
                    "topics": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "textbooks": {"type": "array", "items": {"type": "string"}},
        "references": {"type": "array", "items": {"type": "string"}},
        "experiments": {"type": "array", "items": {"type": "string"}},
    },
}

QUESTIONS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["questions"],
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["text", "marks", "bloom_level", "topic", "answer_key"],
                "properties": {
                    "text": {"type": "string"},
                    "marks": {"type": "integer"},
                    "bloom_level": {"type": "integer"},
                    "topic": {"type": "string"},
                    "answer_key": {"type": "string"},
                },
            },
        }
    },
}


def ai_available():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def _structured_call(system, prompt, schema, max_tokens=16000):
    import anthropic

    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=settings.LMS_AI_MODEL,
        max_tokens=max_tokens,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=system,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        raise ValueError(f"model stopped with {response.stop_reason}")
    text = "".join(block.text for block in response.content if block.type == "text")
    return json.loads(text)


def ai_parse_syllabus(text):
    """Return parsed syllabus dict (same shape as syllabus.parse_syllabus) or None."""
    if not ai_available() or not text.strip():
        return None
    try:
        data = _structured_call(
            "You extract structured course information from engineering university syllabi. "
            "Copy wording from the syllabus; do not invent units, topics or outcomes. "
            "Use 0 for unknown numbers and empty strings/lists for missing sections. "
            "Split each unit's content into short individual topics.",
            f"<syllabus>\n{text}\n</syllabus>",
            SYLLABUS_SCHEMA,
        )
        if not data.get("units"):
            return None
        data.setdefault("online", [])
        for key in ("credits", "lecture_hours", "tutorial_hours", "practical_hours"):
            if not data.get(key):
                data[key] = None
        for u in data["units"]:
            u["hours"] = u.get("hours") or None
        return data
    except Exception:  # network, auth, schema - fall back to heuristics
        logger.exception("AI syllabus parsing failed; using heuristic parser")
        return None


def ai_generate_questions(course, unit, count=8):
    """Return a list of question dicts for a unit, or None."""
    if not ai_available():
        return None
    topics = "\n".join(f"- {t.title}" for t in unit.topics.all())
    outcomes = "\n".join(f"{o.code}: {o.description}" for o in course.outcomes.all())
    try:
        data = _structured_call(
            "You are an experienced engineering professor writing university exam questions. "
            "Write clear, unambiguous questions suitable for a written exam, spread across Bloom's "
            "levels 1-6, with marks of 2, 5 or 10. Numerical questions must include all data needed. "
            "The answer_key is a brief marking scheme.",
            f"Course: {course.code} {course.title} (year {course.year} engineering)\n"
            f"Course outcomes:\n{outcomes}\n\nUnit {unit.number}: {unit.title}\nTopics:\n{topics}\n\n"
            f"Write {count} questions for this unit. Set `topic` to the exact topic text it tests.",
            QUESTIONS_SCHEMA,
        )
        return data.get("questions") or None
    except Exception:
        logger.exception("AI question generation failed; using templates")
        return None
