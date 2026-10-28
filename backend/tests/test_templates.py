"""Templates and merge fields."""

from __future__ import annotations

import pytest

from app.engine import templates
from tests import factories


def test_render_replaces_known_fields():
    rendered = templates.render(
        "Hi {{contact.first_name}} — {{client.name}}",
        {"contact": {"first_name": "Alex"}, "client": {"name": "Acme"}},
    )
    assert rendered == "Hi Alex — Acme"


def test_render_raises_on_missing_field():
    with pytest.raises(templates.MissingField):
        templates.render("Hi {{contact.first_name}}", {"contact": {}})


def test_render_raises_on_missing_root():
    with pytest.raises(templates.MissingField):
        templates.render("{{nope.value}}", {})


def test_validate_fields_reports_unknown_paths():
    unknown = templates.validate_fields("{{contact.first_name}} {{contact.nickname}} {{bogus.x}}")
    assert unknown == ["bogus.x", "contact.nickname"]


def test_validate_fields_accepts_the_documented_vocabulary():
    text = (
        "{{contact.first_name}} {{contact.company}} {{persona.from_name}} "
        "{{persona.signature}} {{client.name}} {{signal.role_title}} "
        "{{enum.hours_since_last_update}}"
    )
    assert templates.validate_fields(text) == []


def test_used_fields_is_deduplicated():
    assert templates.used_fields("{{a.b}} {{a.b}}") == {"a.b"}


def test_build_context_from_objects(db, workspace):
    contact = workspace["contact"]
    persona = workspace["persona"]
    client = workspace["client"]

    context = templates.build_context(contact=contact, persona=persona, client=client)
    assert context["contact"]["first_name"] == "Alex"
    assert context["client"]["name"] == "Test Client"
    # from_name is "Dana Tan"; the greeting field takes the first name only.
    assert context["persona"]["first_name"] == "Dana"
    assert context["contact"]["full_name"] == "Alex Ng"


def test_build_context_defaults_signal_role_title(db, workspace):
    context = templates.build_context(contact=workspace["contact"])
    assert context["signal"]["role_title"] == "engineering"


def test_rendered_template_uses_signal_from_custom_fields(db, workspace):
    contact = workspace["contact"]
    contact.custom_fields = {"role_title": "Mechanical Engineer"}
    context = templates.build_context(contact=contact)
    assert templates.render("{{signal.role_title}}", context) == "engineering"
    context["signal"]["role_title"] = contact.custom_fields["role_title"]
    assert templates.render("{{signal.role_title}}", context) == "Mechanical Engineer"


def test_seed_templates_all_validate():
    """The authored copy in app.seed must pass the same check publishing applies."""
    from app.seed import TEMPLATES

    for spec in TEMPLATES:
        unknown = templates.validate_fields(spec["subject"], spec["body"])
        assert unknown == [], f"{spec['key']} has unknown merge fields: {unknown}"


def test_seed_graph_passes_validation():
    from app.seed import SEQUENCE_GRAPH, TEMPLATES

    from app.engine.graph import blocking_issues, validate_graph

    template_map = {
        spec["key"]: {
            "channel": spec["channel"],
            "subject": spec["subject"],
            "body": spec["body"],
        }
        for spec in TEMPLATES
    }
    issues = validate_graph(SEQUENCE_GRAPH, templates=template_map)
    assert blocking_issues(issues) == [], [i.message for i in issues]
