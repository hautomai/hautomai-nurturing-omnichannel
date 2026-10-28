"""Publish-time validation (spec 8.7)."""

from __future__ import annotations

from app import constants as c
from app.engine.graph import blocking_issues, parse_graph, validate_graph
from tests import factories

TEMPLATES = {
    "email_1": {"channel": c.CHANNEL_EMAIL, "subject": "Hi {{contact.first_name}}", "body": "Body"},
    "email_2": {"channel": c.CHANNEL_EMAIL, "subject": "Two", "body": "Body"},
    "email_1_sms": {"channel": c.CHANNEL_SMS, "subject": "", "body": "Body"},
    "sms_1": {"channel": c.CHANNEL_SMS, "subject": "", "body": "Body"},
    "sms_1_email": {"channel": c.CHANNEL_EMAIL, "subject": "Sub", "body": "Body"},
}


def codes(issues) -> set[str]:
    return {issue.code for issue in issues}


def test_valid_graph_has_no_errors():
    issues = validate_graph(factories.three_touch_graph(), templates=TEMPLATES)
    assert blocking_issues(issues) == []


def test_empty_graph_is_rejected():
    assert "graph_empty" in codes(validate_graph({}, templates=TEMPLATES))


def test_entry_point_must_exist():
    graph = factories.three_touch_graph()
    graph["entry_step_keys"] = ["nope"]
    assert "entry_unknown" in codes(validate_graph(graph, templates=TEMPLATES))


def test_missing_entry_point_is_rejected():
    graph = factories.three_touch_graph()
    graph["entry_step_keys"] = []
    assert "entry_missing" in codes(validate_graph(graph, templates=TEMPLATES))


def test_unknown_template_is_rejected():
    graph = factories.three_touch_graph()
    graph["steps"][0]["template_key"] = "does_not_exist"
    assert "template_unknown" in codes(validate_graph(graph, templates=TEMPLATES))


def test_template_channel_mismatch_is_rejected():
    graph = factories.three_touch_graph()
    graph["steps"][0]["template_key"] = "sms_1"  # an SMS template on an email step
    assert "template_channel_mismatch" in codes(validate_graph(graph, templates=TEMPLATES))


def test_unknown_merge_field_blocks_publish():
    templates = dict(TEMPLATES)
    templates["email_1"] = {
        "channel": c.CHANNEL_EMAIL,
        "subject": "Hi {{contact.nickname}}",
        "body": "{{contact.first_name}}",
    }
    issues = validate_graph(factories.three_touch_graph(), templates=templates)
    assert "template_field_unknown" in codes(issues)


def test_send_step_requires_a_fallback():
    graph = factories.three_touch_graph()
    del graph["steps"][0]["fallback"]
    assert "fallback_missing" in codes(validate_graph(graph, templates=TEMPLATES))


def test_same_channel_fallback_is_pointless():
    graph = factories.three_touch_graph()
    graph["steps"][0]["fallback"] = "send_email"
    graph["steps"][0]["fallback_template_key"] = "email_2"
    assert "fallback_pointless" in codes(validate_graph(graph, templates=TEMPLATES))


def test_fallback_needs_a_template():
    graph = factories.three_touch_graph()
    del graph["steps"][0]["fallback_template_key"]
    assert "fallback_template_missing" in codes(validate_graph(graph, templates=TEMPLATES))


def test_fallback_template_channel_must_match():
    graph = factories.three_touch_graph()
    # step_sms_1 falls back to email, so its fallback template must be an email one.
    graph["steps"][2]["fallback_template_key"] = "sms_1"
    assert "fallback_channel_mismatch" in codes(validate_graph(graph, templates=TEMPLATES))


def test_unreachable_step_is_rejected():
    graph = factories.three_touch_graph()
    graph["steps"].append(factories.exit_step("step_floating"))
    assert "step_unreachable" in codes(validate_graph(graph, templates=TEMPLATES))


def test_graph_needs_an_exit():
    graph = factories.three_touch_graph()
    graph["steps"] = [s for s in graph["steps"] if s["type"] != "exit"]
    graph["steps"][-1]["next"] = "step_email_1"  # also makes a cycle
    issues = codes(validate_graph(graph, templates=TEMPLATES))
    assert "exit_missing" in issues


def test_every_step_must_reach_an_exit():
    graph = factories.three_touch_graph()
    graph["steps"].append(factories.delay_step("step_dead_end", "step_dead_end"))
    graph["steps"][0]["next"] = "step_dead_end"
    issues = codes(validate_graph(graph, templates=TEMPLATES))
    assert "no_exit_path" in issues


def test_cycle_without_a_guard_is_rejected():
    graph = factories.three_touch_graph()
    graph["steps"].append(factories.delay_step("step_loop", "step_loop"))
    graph["steps"][0]["next"] = "step_loop"
    assert "cycle_unguarded" in codes(validate_graph(graph, templates=TEMPLATES))


def test_cycle_with_a_guard_is_allowed():
    graph = factories.three_touch_graph()
    loop = factories.delay_step("step_loop", "step_loop")
    loop["max_iterations"] = 3
    graph["steps"].append(loop)
    graph["steps"][0]["next"] = "step_loop"
    assert "cycle_unguarded" not in codes(validate_graph(graph, templates=TEMPLATES))


def test_unknown_predicate_is_rejected():
    graph = factories.three_touch_graph()
    graph["steps"].append(
        factories.condition_step("step_bad", "step_exit", "step_exit", {"type": "opened_email"})
    )
    graph["steps"][0]["next"] = "step_bad"
    assert "predicate_unknown" in codes(validate_graph(graph, templates=TEMPLATES))


def test_missing_reference_is_rejected():
    graph = factories.three_touch_graph()
    graph["steps"][0]["next"] = "nowhere"
    assert "reference_unknown" in codes(validate_graph(graph, templates=TEMPLATES))


def test_reaction_target_must_exist():
    graph = factories.three_touch_graph()
    graph["reactions"] = {"replied_positive": "not_a_step"}
    assert "reaction_target_unknown" in codes(validate_graph(graph, templates=TEMPLATES))


def test_cadence_cap_blocks_by_default():
    graph = {
        "entry_step_keys": ["s1"],
        "steps": [
            factories.email_step("s1", "s2", template="email_1"),
            factories.email_step("s2", "s3", template="email_2"),
            factories.sms_step("s3", "s4", template="sms_1"),
            factories.sms_step("s4", "s5", template="sms_1"),
            factories.sms_step("s5", "s6", template="sms_1"),
            factories.email_step("s6", "s7", template="email_1"),
            factories.email_step("s7", "s8", template="email_2"),
            factories.email_step("s8", "s9", template="email_1"),
            factories.exit_step("s9"),
        ],
    }
    issues = validate_graph(graph, templates=TEMPLATES)
    assert "cadence_cap_exceeded" in codes(issues)
    assert blocking_issues(issues)


def test_cadence_cap_becomes_a_warning_with_an_override():
    graph = {
        "entry_step_keys": ["s1"],
        "steps": [
            factories.email_step("s1", "s2", template="email_1"),
            factories.email_step("s2", "s3", template="email_2"),
            factories.sms_step("s3", "s4", template="sms_1"),
            factories.sms_step("s4", "s5", template="sms_1"),
            factories.sms_step("s5", "s6", template="sms_1"),
            factories.email_step("s6", "s7", template="email_1"),
            factories.email_step("s7", "s8", template="email_2"),
            factories.email_step("s8", "s9", template="email_1"),
            factories.exit_step("s9"),
        ],
    }
    issues = validate_graph(graph, templates=TEMPLATES, allow_cadence_override=True)
    assert "cadence_cap_exceeded" not in codes(issues)
    assert "cadence_cap_overridden" in codes(issues)
    assert not blocking_issues(issues)


def test_duplicate_step_keys_are_rejected():
    graph = factories.three_touch_graph()
    graph["steps"].append(dict(graph["steps"][0]))
    assert "step_key_missing" in codes(validate_graph(graph, templates=TEMPLATES))


def test_graph_helpers():
    graph = parse_graph(factories.three_touch_graph())
    assert graph.channel_of("step_email_1") == c.CHANNEL_EMAIL
    assert graph.channel_of("step_wait_1") == ""
    assert graph.delay_seconds("step_wait_1") == 86400
    assert graph.send_step_keys() == ["step_email_1", "step_sms_1", "step_email_2"]
    assert graph.counts() == (3, 1)
