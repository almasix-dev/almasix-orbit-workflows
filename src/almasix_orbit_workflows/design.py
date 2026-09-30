"""Edits the designer and the Python API both apply to the same document dict."""

from __future__ import annotations

from typing import Any

from almasix_orbit_workflows.document import check_document


def add_status(document: dict[str, Any], key: str, label: str, *, color: str = "gray", terminal: bool = False) -> dict[str, Any]:
    document.setdefault("statuses", []).append({"key": key, "label": label, "color": color, "terminal": terminal})
    return document


def add_step(document: dict[str, Any], key: str, kind: str = "form") -> dict[str, Any]:
    document.setdefault("steps", []).append(
        {
            "key": key,
            "kind": kind,
            "assignees": [],
            "policy": "any",
            "quorum": 1,
            "schema": [],
            "edges": [],
            "escalate": None,
            "reminders": [],
            "join_from": [],
            "join_policy": "all",
            "sign_outcomes": [],
            "intent": "I agree to the contents of this step.",
        }
    )
    document.setdefault("start", key)
    return document


def connect(
    document: dict[str, Any],
    step_key: str,
    outcome: str,
    *,
    to: str,
    status: str,
    label: str,
    branch: str = "finish",
) -> dict[str, Any]:
    step = _find(document, step_key)
    step["edges"].append(
        {"key": outcome, "to": to, "status": status, "label": label, "when": None, "effect": None, "start_workflow": None, "branch": branch}
    )
    return document


def assign(document: dict[str, Any], step_key: str, spec: dict[str, str]) -> dict[str, Any]:
    _find(document, step_key)["assignees"].append(dict(spec))
    return document


def place_field(document: dict[str, Any], step_key: str, node: dict[str, Any]) -> dict[str, Any]:
    """Append a component at the root of the step schema. Nest by passing a layout node."""
    _find(document, step_key)["schema"].append(node)
    return document


def problems(document: dict[str, Any]) -> list[str]:
    return check_document(document)


def _find(document: dict[str, Any], key: str) -> dict[str, Any]:
    for step in document.get("steps") or []:
        if step["key"] == key:
            return step
    raise KeyError(key)
