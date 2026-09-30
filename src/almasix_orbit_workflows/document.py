"""The published document: statuses, steps, and edges. Python and the designer share it."""

from __future__ import annotations

from typing import Any, Self

from almasix_orbit_workflows.clock import ensure_zone
from almasix_orbit_workflows.errors import DocumentError

KINDS = frozenset({"form", "approval", "sign", "route", "notify", "fork", "join"})
POLICIES = frozenset({"any", "all", "quorum"})
ASSIGNEE_KINDS = frozenset({"user", "role", "starter", "field", "expression", "guest"})
BRANCH_ENDS = frozenset({"finish", "abort"})


def user(user_id: str) -> dict[str, str]:
    return {"kind": "user", "value": str(user_id)}


def role(name: str) -> dict[str, str]:
    return {"kind": "role", "value": str(name)}


def starter() -> dict[str, str]:
    return {"kind": "starter", "value": ""}


def field(name: str) -> dict[str, str]:
    """Assign whoever was picked in an earlier answer named ``name``."""
    return {"kind": "field", "value": str(name)}


def expression(text: str) -> dict[str, str]:
    return {"kind": "expression", "value": str(text)}


def guest(email: str, name: str = "") -> dict[str, str]:
    return {"kind": "guest", "value": str(email), "label": name}


class Step:
    """One node. Build it, then hand it to :meth:`Workflow.step`."""

    def __init__(self, key: str, kind: str = "form") -> None:
        self.key = key
        self.kind = kind
        self.assignees: list[dict[str, str]] = []
        self.policy = "any"
        self.quorum = 1
        self._schema: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []
        self._escalate: dict[str, str] | None = None
        self.reminders: list[str] = []
        self.join_from: list[str] = []
        self.join_policy = "all"
        self.sign_outcomes: list[str] = []
        self.intent = "I agree to the contents of this step."

    @classmethod
    def make(cls, key: str, kind: str = "form") -> Self:
        return cls(key, kind)

    def assignee(self, spec: dict[str, str]) -> Self:
        self.assignees.append(dict(spec))
        return self

    def how_many(self, policy: str, quorum: int = 1) -> Self:
        """``any``, ``all``, or ``quorum`` of the resolved people must finish the step."""
        self.policy = policy
        self.quorum = quorum
        return self

    def schema(self, nodes: list[dict[str, Any]]) -> Self:
        self._schema = list(nodes)
        return self

    def on(
        self,
        key: str,
        *,
        to: str,
        status: str,
        label: str,
        when: str | None = None,
        effect: str | None = None,
        start_workflow: str | None = None,
        branch: str = "finish",
    ) -> Self:
        """An outcome. ``branch`` is ``finish`` (siblings stay) or ``abort`` (siblings stop)."""
        self.edges.append(
            {
                "key": key,
                "to": to,
                "status": status,
                "label": label,
                "when": when,
                "effect": effect,
                "start_workflow": start_workflow,
                "branch": branch,
            }
        )
        return self

    def escalate(self, *, after: str, to: str, status: str, label: str = "Escalated") -> Self:
        self._escalate = {"after": after, "to": to, "status": status, "label": label}
        return self

    def remind(self, *durations: str) -> Self:
        self.reminders.extend(durations)
        return self

    def joins(self, steps: list[str], policy: str = "all", quorum: int = 1) -> Self:
        self.join_from = list(steps)
        self.join_policy = policy
        self.quorum = quorum
        return self

    def signs(self, *outcome_keys: str) -> Self:
        """Outcomes that require a signature. Defaults to every edge on a sign step."""
        self.sign_outcomes = list(outcome_keys)
        return self

    def statement(self, text: str) -> Self:
        self.intent = text
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "assignees": [dict(item) for item in self.assignees],
            "policy": self.policy,
            "quorum": self.quorum,
            "schema": self._schema,
            "edges": [dict(item) for item in self.edges],
            "escalate": dict(self._escalate) if self._escalate else None,
            "reminders": list(self.reminders),
            "join_from": list(self.join_from),
            "join_policy": self.join_policy,
            "sign_outcomes": list(self.sign_outcomes),
            "intent": self.intent,
        }


class CaseAction:
    """Withdraw or an operator move. The status key still comes from this document."""

    def __init__(self, key: str, *, status: str, label: str, who: str) -> None:
        self.key = key
        self.status = status
        self.label = label
        self.who = who

    def to_dict(self) -> dict[str, str]:
        return {"key": self.key, "status": self.status, "label": self.label, "who": self.who}


class Workflow:
    """A definition before it is published. Call :meth:`document` and then the engine."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.name = key
        self.timezone = "UTC"
        self.revisit_limit = 10
        self.stuck_status: str | None = None
        self._statuses: list[dict[str, Any]] = []
        self._steps: list[Step] = []
        self._start: str | None = None
        self._watchers: list[dict[str, str]] = []
        self._actions: list[CaseAction] = []

    @classmethod
    def make(cls, key: str) -> Self:
        return cls(key)

    def titled(self, name: str) -> Self:
        self.name = name
        return self

    def zone(self, name: str) -> Self:
        self.timezone = name
        return self

    def limit_visits(self, count: int, *, stuck_status: str | None = None) -> Self:
        self.revisit_limit = count
        self.stuck_status = stuck_status
        return self

    def status(self, key: str, label: str, *, color: str = "gray", terminal: bool = False) -> Self:
        self._statuses.append({"key": key, "label": label, "color": color, "terminal": terminal})
        return self

    def step(self, step: Step) -> Self:
        self._steps.append(step)
        if self._start is None:
            self._start = step.key
        return self

    def start(self, step_key: str) -> Self:
        self._start = step_key
        return self

    def watcher(self, spec: dict[str, str]) -> Self:
        self._watchers.append(dict(spec))
        return self

    def case_action(self, key: str, *, status: str, label: str, who: str) -> Self:
        """``who`` is ``starter``, ``operate``, or ``role:<name>``."""
        self._actions.append(CaseAction(key, status=status, label=label, who=who))
        return self

    def document(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "timezone": self.timezone,
            "revisit_limit": self.revisit_limit,
            "stuck_status": self.stuck_status,
            "statuses": [dict(item) for item in self._statuses],
            "steps": [step.to_dict() for step in self._steps],
            "start": self._start,
            "watchers": [dict(item) for item in self._watchers],
            "case_actions": [item.to_dict() for item in self._actions],
        }


def check_document(document: dict[str, Any]) -> list[str]:
    """Return every reason this document cannot be published. Empty means it can."""
    errors: list[str] = []
    statuses = {item["key"]: item for item in document.get("statuses") or []}
    if not statuses:
        errors.append("Add at least one status.")
    if not any(item.get("terminal") for item in statuses.values()):
        errors.append("Mark one status terminal so a case can finish.")
    steps = {item["key"]: item for item in document.get("steps") or []}
    if not steps:
        errors.append("Add at least one step.")
    keys = [item.get("key") for item in document.get("steps") or []]
    duplicates = {key for key in keys if key and keys.count(key) > 1}
    for key in duplicates:
        errors.append(f"Step '{key}' is listed twice.")
    start = document.get("start")
    if start not in steps:
        errors.append("The start step does not exist.")
    try:
        ensure_zone(str(document.get("timezone") or "UTC"))
    except Exception as exc:  # noqa: BLE001 — Invalid becomes a publish error
        errors.append(str(exc))
    if document.get("stuck_status") and document["stuck_status"] not in statuses:
        errors.append("The stuck status is not in this document.")
    for step in steps.values():
        errors.extend(_check_step(step, steps, statuses))
    if start in steps:
        errors.extend(_reachability(start, steps))
    for action in document.get("case_actions") or []:
        if action.get("status") not in statuses:
            errors.append(f"Case action '{action.get('key')}' uses an unknown status.")
    return errors


def _check_step(step: dict[str, Any], steps: dict[str, Any], statuses: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    kind = step.get("kind")
    if kind not in KINDS:
        errors.append(f"Step '{step.get('key')}' has an unknown kind.")
        return errors
    if step.get("policy") not in POLICIES:
        errors.append(f"Step '{step['key']}' needs a policy of any, all, or quorum.")
    if step.get("policy") == "quorum" and int(step.get("quorum") or 0) < 1:
        errors.append(f"Step '{step['key']}' needs a quorum of at least 1.")
    for person in step.get("assignees") or []:
        if person.get("kind") not in ASSIGNEE_KINDS:
            errors.append(f"Step '{step['key']}' has an unknown assignee.")
    edges = step.get("edges") or []
    if kind == "route" and not any(not edge.get("when") for edge in edges):
        errors.append(f"Route '{step['key']}' needs a default edge with no condition.")
    if kind == "notify" and len(edges) != 1:
        errors.append(f"Notice '{step['key']}' needs exactly one edge.")
    if kind == "join" and not step.get("join_from"):
        errors.append(f"Join '{step['key']}' must name the branches it waits for.")
    if kind == "join" and step.get("join_policy") not in POLICIES:
        errors.append(f"Join '{step['key']}' needs a policy of any, all, or quorum.")
    for edge in edges:
        errors.extend(_check_edge(step["key"], edge, steps, statuses))
    escalate = step.get("escalate")
    if escalate:
        if escalate.get("to") not in steps and escalate.get("to") != "end":
            errors.append(f"Escalation on '{step['key']}' points nowhere.")
        if escalate.get("status") not in statuses:
            errors.append(f"Escalation on '{step['key']}' uses an unknown status.")
    return errors


def _check_edge(step_key: str, edge: dict[str, Any], steps: dict[str, Any], statuses: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    target = edge.get("to")
    if target != "end" and target not in steps:
        errors.append(f"Edge '{edge.get('key')}' on '{step_key}' points nowhere.")
    status = statuses.get(edge.get("status"))
    if status is None:
        errors.append(f"Edge '{edge.get('key')}' on '{step_key}' uses an unknown status.")
    elif target == "end" and not status.get("terminal"):
        errors.append(f"Edge '{edge.get('key')}' ends the case on a status that is not terminal.")
    if edge.get("branch") not in BRANCH_ENDS:
        errors.append(f"Edge '{edge.get('key')}' must finish or abort the branch.")
    return errors


def _reachability(start: str, steps: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        key = stack.pop()
        if key in seen or key not in steps:
            continue
        seen.add(key)
        step = steps[key]
        for edge in step.get("edges") or []:
            if edge.get("to") in steps:
                stack.append(edge["to"])
        escalate = step.get("escalate")
        if escalate and escalate.get("to") in steps:
            stack.append(escalate["to"])
    missing = [key for key in steps if key not in seen]
    return [f"Step '{key}' cannot be reached from the start." for key in missing]


def require_valid(document: dict[str, Any]) -> dict[str, Any]:
    errors = check_document(document)
    if errors:
        raise DocumentError(errors)
    return document
