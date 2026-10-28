"""Static templates with merge fields.

No LLM runs at message time (D13): copy is authored up front and rendered with
``{{ path }}`` merge fields. Validation is strict so a sequence cannot be
published with a field the renderer cannot resolve.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

MERGE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")

# Which merge fields exist. Keep this list in step with the seed templates and
# the dashboard's template preview.
ALLOWED_FIELDS: dict[str, set[str]] = {
    "contact": {
        "first_name",
        "last_name",
        "full_name",
        "email",
        "phone_e164",
        "company",
        "job_title",
        "timezone",
    },
    "persona": {
        "first_name",
        "display_name",
        "from_name",
        "from_email",
        "signature",
        "calendar_link",
    },
    "client": {"name"},
    "signal": {"role_title", "source", "company", "posted_at"},
    "enum": {"hours_since_last_update"},
}


class TemplateError(Exception):
    """Raised when a template cannot be rendered."""


class MissingField(TemplateError):
    def __init__(self, path: str) -> None:
        super().__init__(f"Missing merge field: {path}")
        self.path = path


def validate_fields(*texts: str) -> list[str]:
    """Return the unknown merge-field paths found in the given strings."""
    unknown: list[str] = []
    for text in texts:
        if not text:
            continue
        for path in MERGE_PATTERN.findall(text):
            root, _, rest = path.partition(".")
            if root not in ALLOWED_FIELDS:
                unknown.append(path)
                continue
            if rest not in ALLOWED_FIELDS[root]:
                unknown.append(path)
    return sorted(set(unknown))


def used_fields(*texts: str) -> set[str]:
    found: set[str] = set()
    for text in texts:
        if text:
            found.update(MERGE_PATTERN.findall(text))
    return found


def render(text: str, context: dict[str, Any]) -> str:
    """Replace merge fields, raising ``MissingField`` when one is absent.

    An unknown-but-allowed field that resolves to an empty string is fine; a
    field that is entirely absent from the context is an error, because silently
    sending "Hi ," is worse than failing the step.
    """
    if not text:
        return ""

    def replace(match: re.Match[str]) -> str:
        path = match.group(1)
        root, _, rest = path.partition(".")
        if root not in context:
            raise MissingField(path)
        node = context[root]
        if isinstance(node, dict):
            if rest not in node:
                raise MissingField(path)
            value = node[rest]
        else:
            value = node
        return "" if value is None else str(value)

    return MERGE_PATTERN.sub(replace, text)


@dataclass
class RenderedMessage:
    subject: str = ""
    body: str = ""
    warnings: list[str] = field(default_factory=list)


def build_context(
    *,
    contact: Any,
    persona: Any | None = None,
    client: Any | None = None,
    signal: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the merge context for a contact from ORM-ish objects.

    Accepts either ORM instances or plain dicts so tests and the simulator can
    both use it.
    """

    def get(source: Any, name: str, default: Any = "") -> Any:
        if source is None:
            return default
        if isinstance(source, dict):
            return source.get(name, default)
        return getattr(source, name, default)

    contact_ctx = {
        "first_name": get(contact, "first_name"),
        "last_name": get(contact, "last_name"),
        "email": get(contact, "email"),
        "phone_e164": get(contact, "phone_e164"),
        "company": get(contact, "company"),
        "job_title": get(contact, "job_title"),
        "timezone": get(contact, "timezone"),
    }
    contact_ctx["full_name"] = " ".join(
        part for part in [contact_ctx["first_name"], contact_ctx["last_name"]] if part
    ).strip()

    persona_ctx = {
        "first_name": get(persona, "from_name", ""),
        "display_name": get(persona, "display_name", ""),
        "from_name": get(persona, "from_name", ""),
        "from_email": get(persona, "from_email", ""),
        "signature": get(persona, "signature_html", ""),
        "calendar_link": get(persona, "calendar_link", ""),
    }
    # ``from_name`` is often "Dana Tan"; first name is what the copy greets with.
    if persona_ctx["first_name"]:
        persona_ctx["first_name"] = str(persona_ctx["first_name"]).split(" ")[0]

    context: dict[str, Any] = {
        "contact": contact_ctx,
        "persona": persona_ctx,
        "client": {"name": get(client, "name", "")},
        "signal": {
            "role_title": (signal or {}).get("role_title", "engineering"),
            "source": (signal or {}).get("source", ""),
            "company": (signal or {}).get("company", contact_ctx["company"]),
            "posted_at": (signal or {}).get("posted_at", ""),
        },
        "enum": {"hours_since_last_update": (extra or {}).get("hours_since_last_update", "")},
    }
    if extra:
        for key, value in extra.items():
            if key in context and isinstance(context[key], dict) and isinstance(value, dict):
                context[key].update(value)
    return context
