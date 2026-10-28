"""The sequence graph: node vocabulary, traversal helpers and validation.

A sequence version's ``graph`` is a plain JSON document:

    {
      "entry_step_keys": ["step_email_1"],
      "steps": [
        {"key": "step_email_1", "type": "send_email", "name": "Opener",
         "template_key": "Acme_email_1", "fallback": "send_sms",
         "fallback_template_key": "Acme_email_1_text", "next": "step_delay_1"},
        {"key": "step_delay_1", "type": "delay", "amount": 3, "unit": "days",
         "next": "step_email_2"},
        {"key": "step_cond_1", "type": "condition",
         "predicate": {"type": "no_reply_for", "days": 3},
         "on_true": "step_sms_1", "on_false": "step_exit_1"}
      ],
      "reactions": {"replied_positive": "step_positive"}
    }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app import constants as c
from app.engine import templates as tpl

# --------------------------------------------------------------------------
# Node vocabulary (v1). Open/click tracking nodes are deliberately absent: with
# no pixels or link rewriting they could never fire (D15).
# --------------------------------------------------------------------------
NODE_SEND_EMAIL = "send_email"
NODE_SEND_SMS = "send_sms"
NODE_DELAY = "delay"
NODE_CONDITION = "condition"
NODE_NOTIFY_SLACK = "notify_slack"
NODE_HUMAN_TASK = "human_task"
NODE_EXIT = "exit"

NODE_TYPES = (
    NODE_SEND_EMAIL,
    NODE_SEND_SMS,
    NODE_DELAY,
    NODE_CONDITION,
    NODE_NOTIFY_SLACK,
    NODE_HUMAN_TASK,
    NODE_EXIT,
)

SEND_NODE_CHANNEL: dict[str, str] = {
    NODE_SEND_EMAIL: c.CHANNEL_EMAIL,
    NODE_SEND_SMS: c.CHANNEL_SMS,
}
SEND_CHANNEL_NODE: dict[str, str] = {value: key for key, value in SEND_NODE_CHANNEL.items()}

TERMINAL_TYPES = (NODE_EXIT,)

FALLBACKS = ("send_email", "send_sms", "skip", "halt")

DELAY_UNIT_SECONDS = {"minutes": 60, "hours": 3600, "days": 86400, "weeks": 604800}
DELAY_UNITS = tuple(DELAY_UNIT_SECONDS)

PREDICATE_TYPES = (
    "replied",
    "reply_sentiment",
    "no_reply_for",
    "meeting_booked",
    "channel_available",
    "has_tag",
    "custom_field_equals",
)

# Reaction keys the graph may override. Anything not listed falls through to the
# built-in routing table in ``routing.py``.
REACTION_KEYS = (
    "replied_positive",
    "replied_negative",
    "replied_neutral",
    "replied",
    "auto_reply",
    "meeting_booked",
    "sms_undelivered",
    "bounced_hard",
    "unsubscribed",
)


class GraphError(Exception):
    """Structural problem that makes the graph unusable, not just invalid."""


@dataclass
class Issue:
    level: str  # "error" | "warning"
    code: str
    message: str
    step_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "step_key": self.step_key,
        }


@dataclass
class Graph:
    raw: dict[str, Any] = field(default_factory=dict)
    entry_step_keys: list[str] = field(default_factory=list)
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)

    # -- construction ------------------------------------------------------
    @classmethod
    def parse(cls, raw: dict[str, Any] | None) -> Graph:
        raw = raw or {}
        steps: dict[str, dict[str, Any]] = {}
        for step in raw.get("steps") or []:
            if isinstance(step, dict) and isinstance(step.get("key"), str) and step["key"]:
                steps[step["key"]] = step
        entries = [k for k in (raw.get("entry_step_keys") or []) if isinstance(k, str)]
        return cls(raw=raw, entry_step_keys=entries, steps=steps)

    # -- accessors ---------------------------------------------------------
    def get(self, key: str | None) -> dict[str, Any] | None:
        if not key:
            return None
        return self.steps.get(key)

    def step_type(self, key: str | None) -> str:
        step = self.get(key) or {}
        return str(step.get("type", ""))

    def name_of(self, key: str | None) -> str:
        step = self.get(key) or {}
        return str(step.get("name") or key or "")

    def channel_of(self, key: str | None) -> str:
        return SEND_NODE_CHANNEL.get(self.step_type(key), "")

    def template_key_of(self, key: str | None) -> str:
        step = self.get(key) or {}
        return str(step.get("template_key", ""))

    def fallback_of(self, key: str | None) -> str:
        step = self.get(key) or {}
        return str(step.get("fallback", ""))

    def fallback_template_key_of(self, key: str | None) -> str:
        step = self.get(key) or {}
        return str(step.get("fallback_template_key", ""))

    def delay_seconds(self, key: str | None) -> int:
        step = self.get(key) or {}
        amount = step.get("amount", 0)
        unit = str(step.get("unit", "days"))
        try:
            amount_value = float(amount)
        except (TypeError, ValueError):
            amount_value = 0.0
        return int(amount_value * DELAY_UNIT_SECONDS.get(unit, 86400))

    def successors(self, key: str | None) -> list[str]:
        step = self.get(key)
        if not step:
            return []
        step_type = str(step.get("type", ""))
        if step_type == NODE_CONDITION:
            return [k for k in (step.get("on_true"), step.get("on_false")) if isinstance(k, str)]
        if step_type in (NODE_EXIT,):
            return []
        targets = [step.get("next")]
        if step_type == NODE_HUMAN_TASK:
            targets.append(step.get("on_timeout_next"))
        return [k for k in targets if isinstance(k, str)]

    def is_terminal(self, key: str | None) -> bool:
        return self.step_type(key) in TERMINAL_TYPES

    def ordered_keys(self) -> list[str]:
        """Breadth-first order from the entry points; handy for the funnel UI."""
        seen: list[str] = []
        visited: set[str] = set()
        queue = list(self.entry_step_keys)
        while queue:
            key = queue.pop(0)
            if key in visited or key not in self.steps:
                continue
            visited.add(key)
            seen.append(key)
            queue.extend(self.successors(key))
        # Include anything unreachable so the API never silently hides a step.
        for key in self.steps:
            if key not in visited:
                seen.append(key)
        return seen

    def send_step_keys(self) -> list[str]:
        return [k for k in self.ordered_keys() if self.step_type(k) in SEND_NODE_CHANNEL]

    def reaction_overrides(self) -> dict[str, str]:
        raw = self.raw.get("reactions") or {}
        return {
            key: value
            for key, value in raw.items()
            if key in REACTION_KEYS and isinstance(value, str) and value
        }

    def counts(self) -> tuple[int, int]:
        emails = sum(1 for k in self.send_step_keys() if self.channel_of(k) == c.CHANNEL_EMAIL)
        sms = sum(1 for k in self.send_step_keys() if self.channel_of(k) == c.CHANNEL_SMS)
        return emails + sms, sms


def parse_graph(raw: dict[str, Any] | None) -> Graph:
    return Graph.parse(raw)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def _reverse_edges(graph: Graph) -> dict[str, list[str]]:
    reverse: dict[str, list[str]] = {key: [] for key in graph.steps}
    for key in graph.steps:
        for target in graph.successors(key):
            reverse.setdefault(target, []).append(key)
    return reverse


def _reachable(graph: Graph, starts: list[str]) -> set[str]:
    """Steps reachable going forwards from ``starts``."""
    seen: set[str] = set()
    queue = [s for s in starts if s in graph.steps]
    while queue:
        key = queue.pop()
        if key in seen:
            continue
        seen.add(key)
        queue.extend(graph.successors(key))
    return seen


def _can_reach(reverse: dict[str, list[str]], starts: list[str]) -> set[str]:
    """Steps that can reach one of ``starts`` (walk the edges backwards)."""
    seen: set[str] = set()
    queue = list(starts)
    while queue:
        key = queue.pop()
        if key in seen:
            continue
        seen.add(key)
        queue.extend(reverse.get(key, []))
    return seen


def _find_cycles(graph: Graph) -> list[tuple[str, str]]:
    """Return back edges ``(from, to)``. Each back edge closes a loop."""
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(graph.steps, WHITE)
    back_edges: list[tuple[str, str]] = []

    def visit(node: str) -> None:
        color[node] = GREY
        for target in graph.successors(node):
            if target not in graph.steps:
                continue
            if color[target] == GREY:
                back_edges.append((node, target))
            elif color[target] == WHITE:
                visit(target)
        color[node] = BLACK

    for key in graph.steps:
        if color[key] == WHITE:
            visit(key)
    return back_edges


def validate_graph(
    raw: dict[str, Any] | None,
    *,
    templates: dict[str, dict[str, Any]] | None = None,
    allow_cadence_override: bool = False,
    max_touches: int = 6,
    max_sms: int = 2,
) -> list[Issue]:
    """Return every problem that should block publishing.

    ``templates`` maps template key -> ``{channel, subject, body}`` for the
    client. Unknown keys and unknown merge fields are errors.
    """
    issues: list[Issue] = []
    templates = templates or {}
    graph = parse_graph(raw)

    def error(code: str, message: str, step_key: str | None = None) -> None:
        issues.append(Issue("error", code, message, step_key))

    def warning(code: str, message: str, step_key: str | None = None) -> None:
        issues.append(Issue("warning", code, message, step_key))

    if not isinstance(raw, dict) or not raw:
        error("graph_empty", "The sequence graph is empty.")
        return issues

    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        error("steps_missing", "The graph needs a non-empty 'steps' list.")
        return issues

    if len(graph.steps) != len(raw_steps):
        error(
            "step_key_missing",
            "Every step needs a unique non-empty 'key'. A step_key is also what "
            "keeps in-flight contacts anchored across versions.",
        )

    if not graph.entry_step_keys:
        error("entry_missing", "The graph needs at least one entry point in 'entry_step_keys'.")

    for key in graph.entry_step_keys:
        if key not in graph.steps:
            error("entry_unknown", f"Entry point '{key}' does not exist.")

    # -- per-step checks ---------------------------------------------------
    for key, step in graph.steps.items():
        step_type = step.get("type")
        if step_type not in NODE_TYPES:
            error("step_type_unknown", f"Step type '{step_type}' is not supported.", key)
            continue
        if not step.get("name"):
            warning("step_name_missing", "Step has no display name.", key)

        if step_type == NODE_DELAY:
            if not isinstance(step.get("amount"), (int, float)):
                error("delay_amount_invalid", "Delay needs a numeric 'amount'.", key)
            if step.get("unit") not in DELAY_UNITS:
                error(
                    "delay_unit_invalid",
                    f"Delay unit must be one of {', '.join(DELAY_UNITS)}.",
                    key,
                )
            if not step.get("next"):
                error("next_missing", "Delay needs a 'next' step.", key)

        elif step_type in SEND_NODE_CHANNEL:
            template_key = step.get("template_key")
            if not template_key:
                error("template_missing", "Send step needs a 'template_key'.", key)
            elif template_key not in templates:
                error(
                    "template_unknown",
                    f"Template '{template_key}' does not exist for this client.",
                    key,
                )
            else:
                template = templates[template_key]
                expected = SEND_NODE_CHANNEL[step_type]
                if template.get("channel") != expected:
                    error(
                        "template_channel_mismatch",
                        f"Template '{template_key}' is a {template.get('channel')} "
                        f"template but this step sends {expected}.",
                        key,
                    )
                unknown = tpl.validate_fields(
                    str(template.get("subject", "")), str(template.get("body", ""))
                )
                if unknown:
                    error(
                        "template_field_unknown",
                        f"Template '{template_key}' uses unknown merge fields: "
                        f"{', '.join(unknown)}.",
                        key,
                    )

            # An in-flight reaction has to go somewhere: every send step declares
            # an explicit fallback (publish-time requirement from the spec).
            fallback = step.get("fallback")
            if fallback not in FALLBACKS:
                error(
                    "fallback_missing",
                    f"Send step needs a 'fallback' of {', '.join(FALLBACKS)}.",
                    key,
                )
            elif fallback in SEND_NODE_CHANNEL:  # "send_email" / "send_sms"
                if SEND_NODE_CHANNEL[fallback] == SEND_NODE_CHANNEL[step_type]:
                    error(
                        "fallback_pointless",
                        f"Fallback '{fallback}' is the same channel as this step.",
                        key,
                    )
                fallback_template = step.get("fallback_template_key")
                expected_channel = SEND_NODE_CHANNEL[fallback]
                if not fallback_template:
                    error(
                        "fallback_template_missing",
                        f"Fallback '{fallback}' needs a 'fallback_template_key'.",
                        key,
                    )
                elif fallback_template not in templates:
                    error(
                        "fallback_template_unknown",
                        f"Fallback template '{fallback_template}' does not exist.",
                        key,
                    )
                elif templates[fallback_template].get("channel") != expected_channel:
                    error(
                        "fallback_channel_mismatch",
                        f"Fallback template '{fallback_template}' is a "
                        f"{templates[fallback_template].get('channel')} template but the "
                        f"fallback is {expected_channel}.",
                        key,
                    )
            if not step.get("next"):
                error("next_missing", "Send step needs a 'next' step.", key)

        elif step_type == NODE_CONDITION:
            predicate = step.get("predicate")
            if not isinstance(predicate, dict) or not predicate.get("type"):
                error("predicate_missing", "Condition needs a 'predicate' object.", key)
            elif predicate.get("type") not in PREDICATE_TYPES:
                error(
                    "predicate_unknown",
                    f"Predicate '{predicate.get('type')}' is not supported. "
                    f"Use one of {', '.join(PREDICATE_TYPES)}.",
                    key,
                )
            for branch in ("on_true", "on_false"):
                if not step.get(branch):
                    error("branch_missing", f"Condition needs '{branch}'.", key)

        elif step_type == NODE_NOTIFY_SLACK:
            if not step.get("next"):
                error("next_missing", "Notify step needs a 'next' step.", key)

        elif step_type == NODE_HUMAN_TASK:
            if step.get("on_timeout") not in (None, "exit", "continue"):
                error("task_timeout_invalid", "on_timeout must be 'exit' or 'continue'.", key)

        if step_type not in SEND_NODE_CHANNEL and step.get("fallback"):
            warning("fallback_ignored", "Fallback only applies to send steps.", key)

    # -- reference integrity ----------------------------------------------
    for key in graph.steps:
        for target in graph.successors(key):
            if target not in graph.steps:
                error("reference_unknown", f"Step references missing step '{target}'.", key)

    for reaction, target in graph.reaction_overrides().items():
        if target not in graph.steps:
            error(
                "reaction_target_unknown",
                f"Reaction '{reaction}' points at missing step '{target}'.",
            )

    # -- reachability and termination --------------------------------------
    reachable = _reachable(graph, graph.entry_step_keys)
    for key in graph.steps:
        if key not in reachable:
            error("step_unreachable", "Step cannot be reached from any entry point.", key)

    reverse = _reverse_edges(graph)
    exits = [key for key in graph.steps if graph.is_terminal(key)]
    if not exits:
        error("exit_missing", "The graph has no 'exit' step, so no contact can finish.")
    else:
        # Reverse reachability: which steps can still reach an exit?
        can_finish = _can_reach(reverse, exits)
        for key in graph.steps:
            if key not in can_finish:
                error("no_exit_path", "Step has no path to an exit step.", key)

    # -- cycles need an explicit guard -------------------------------------
    for source, target in _find_cycles(graph):
        guard = graph.get(source) or {}
        if not isinstance(guard.get("max_iterations"), int) or guard.get("max_iterations", 0) < 1:
            error(
                "cycle_unguarded",
                f"Step '{source}' loops back to '{target}' but declares no "
                "'max_iterations' guard.",
                source,
            )

    # -- cadence caps ------------------------------------------------------
    total_touches, sms_touches = graph.counts()
    if total_touches > max_touches or sms_touches > max_sms:
        message = (
            f"Sequence has {total_touches} touches ({sms_touches} SMS); the confirmed "
            f"cadence cap is {max_touches} touches and {max_sms} SMS."
        )
        if allow_cadence_override:
            warning("cadence_cap_overridden", message)
        else:
            error("cadence_cap_exceeded", message)

    return issues


def blocking_issues(issues: list[Issue]) -> list[Issue]:
    return [issue for issue in issues if issue.level == "error"]
