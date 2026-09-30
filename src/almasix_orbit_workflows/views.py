"""HTML for the inbox, a case, a task, and the canvas. Plain markup a panel page can return."""

from __future__ import annotations

from typing import Any

from almasix.orbit.support.html import e
from almasix_orbit_workflows.design import problems
from almasix_orbit_workflows.engine import WorkflowEngine
from almasix_orbit_workflows.schema_tree import render_schema
from almasix_orbit_workflows.store import MemoryStore


def render_inbox(engine: WorkflowEngine, actor_id: str) -> str:
    engine.promote_due()
    rows: list[str] = []
    for task in engine.store.tasks.values():
        if task["state"] != "open":
            continue
        ids = {item["id"] for item in task["assignees"]}
        if ids and actor_id not in ids:
            continue
        case = engine.store.case(task["case_id"]) or {}
        status = _label(engine, case)
        claim = f"Claimed by {e(task['claim'])}" if task.get("claim") else "Available"
        if task.get("unassigned"):
            claim = "Unassigned"
        rows.append(
            f'<li class="wf-task" data-task="{e(task["id"])}">'
            f'<a href="?task={e(task["id"])}">{e(case.get("key", ""))} · {e(task["step"])}</a>'
            f'<span class="wf-status" data-color="{e(status["color"])}">{e(status["label"])}</span>'
            f'<span class="wf-claim">{claim}</span></li>'
        )
    body = "".join(rows) or '<li class="wf-empty">Nothing is waiting on you.</li>'
    return f'<section class="wf-inbox"><h1>Inbox</h1><ul>{body}</ul></section>'


def render_case(engine: WorkflowEngine, case_id: str) -> str:
    case = engine.store.case(case_id)
    if case is None:
        return '<p class="wf-missing">This case does not exist.</p>'
    status = _label(engine, case)
    events = "".join(
        f'<li><time>{e(event["at"])}</time> {e(event["label"])} {e(event["kind"])}</li>'
        for event in engine.store.events_for(case_id)
    )
    tokens = "".join(
        f'<li data-state="{e(task["state"])}">{e(task["step"])}</li>'
        for task in engine.store.tasks_for(case_id)
    )
    signatures = "".join(
        f'<li>{e(row["name"])} <code>{e(row["hash"][:12])}</code></li>'
        for row in engine.store.signatures_for(case_id)
    )
    return (
        f'<article class="wf-case" data-status="{e(case.get("status") or "")}">'
        f'<h1>{e(case["key"])}</h1>'
        f'<p class="wf-status" data-color="{e(status["color"])}">{e(status["label"])}</p>'
        f'<h2>Open work</h2><ul class="wf-tokens">{tokens}</ul>'
        f'<h2>Timeline</h2><ol class="wf-timeline">{events}</ol>'
        f'<h2>Signatures</h2><ul class="wf-signatures">{signatures}</ul>'
        f"</article>"
    )


def render_task(engine: WorkflowEngine, task_id: str, *, guest: bool = False) -> str:
    task = engine.store.task(task_id)
    if task is None or task["state"] != "open":
        return '<p class="wf-missing">This task is not open.</p>'
    case = engine.store.case(task["case_id"]) or {}
    document = engine.document_for(case["key"], case["version"])
    step = next(item for item in document["steps"] if item["key"] == task["step"])
    answers = case.get("answers") or {}
    if guest:
        answers = {key: value for key, value in answers.items() if _field_on(step, key)}
    form = render_schema(step.get("schema") or [], answers)
    buttons = "".join(
        f'<button type="submit" name="outcome" value="{e(key)}">{e(label)}</button>'
        for key, label in (task.get("outcomes") or {}).items()
    )
    return (
        f'<form class="wf-task-form" data-task="{e(task_id)}">'
        f"<h1>{e(step['key'])}</h1>{form}<div class=\"wf-outcomes\">{buttons}</div></form>"
    )


def render_canvas(document: dict[str, Any]) -> str:
    """Steps as nodes and edges as labeled wires. The schema of the selected step is listed under it."""
    issues = problems(document)
    notice = "".join(f"<li>{e(item)}</li>" for item in issues)
    nodes = []
    for step in document.get("steps") or []:
        wires = "".join(
            f'<li>{e(edge["label"])} → {e(edge["to"])} <span data-status="{e(edge["status"])}"></span></li>'
            for edge in step.get("edges") or []
        )
        fields = "".join(f'<li>{e(node.get("type"))} {e(node.get("name") or "")}</li>' for node in step.get("schema") or [])
        nodes.append(
            f'<section class="wf-node" data-kind="{e(step["kind"])}" data-step="{e(step["key"])}">'
            f'<h2>{e(step["key"])}</h2><ul class="wf-wires">{wires}</ul><ul class="wf-schema">{fields}</ul></section>'
        )
    statuses = "".join(
        f'<li data-terminal="{str(bool(item.get("terminal"))).lower()}">{e(item["label"])}</li>'
        for item in document.get("statuses") or []
    )
    return (
        f'<div class="wf-canvas"><aside><h2>Statuses</h2><ul>{statuses}</ul>'
        f'<h2>Checks</h2><ul class="wf-problems">{notice}</ul></aside>'
        f'<div class="wf-nodes">{"".join(nodes)}</div></div>'
    )


def _label(engine: WorkflowEngine, case: dict[str, Any]) -> dict[str, str]:
    if not case or not case.get("key"):
        return {"label": "", "color": "gray"}
    try:
        document = engine.document_for(case["key"], case.get("version"))
    except Exception:  # noqa: BLE001 — a half-built case still renders
        return {"label": case.get("status") or "", "color": "gray"}
    for item in document["statuses"]:
        if item["key"] == case.get("status"):
            return {"label": item["label"], "color": item.get("color") or "gray"}
    return {"label": case.get("status") or "Not started", "color": "gray"}


def _field_on(step: dict[str, Any], name: str) -> bool:
    stack = list(step.get("schema") or [])
    while stack:
        node = stack.pop()
        if node.get("name") == name:
            return True
        stack.extend(node.get("schema") or [])
    return False


def open_tasks(store: MemoryStore, actor_id: str) -> list[dict[str, Any]]:
    return [
        task
        for task in store.tasks.values()
        if task["state"] == "open" and (not task["assignees"] or any(item["id"] == actor_id for item in task["assignees"]))
    ]
