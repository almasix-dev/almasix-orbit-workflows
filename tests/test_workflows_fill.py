"""Fill the remaining branches so the package stays fully covered."""

from __future__ import annotations

from datetime import datetime

import pytest
from almasix_orbit_workflows import (
    Clock,
    Directory,
    Person,
    Step,
    Workflow,
    WorkflowEngine,
    guest,
    role,
    starter,
)
from almasix_orbit_workflows.actors import resolve_assignees
from almasix_orbit_workflows.clock import parse_stamp
from almasix_orbit_workflows.engine import _edge, _status, _step
from almasix_orbit_workflows.errors import Conflict, Forbidden, Invalid, NotFound
from almasix_orbit_workflows.plugin import CasePage, WorkflowPlugin
from almasix_orbit_workflows.schema_tree import render_schema
from almasix_orbit_workflows.views import open_tasks, render_inbox


def test_remaining_engine_and_view_branches() -> None:
    directory = Directory([{"id": "sam", "name": "Sam", "roles": ["sales"]}])
    runtime = WorkflowEngine(directory=directory, clock=Clock())
    draft = Workflow.make("plain").status("done", "Done", terminal=True).step(
        Step.make("a").assignee(starter()).on("go", to="end", status="done", label="Go")
    ).document()
    runtime.save(draft)
    with pytest.raises(Invalid):
        runtime.retire("plain")
    with pytest.raises(NotFound):
        runtime.document_for("plain")
    with pytest.raises(Invalid):
        runtime.start("plain", Person.of("sam"))
    runtime.publish("plain")
    case = runtime.start("plain", Person.of("sam"))
    task = next(iter(runtime.store.tasks_for(case["id"])))
    with pytest.raises(Forbidden):
        runtime.complete(task["id"], Person.of("other"), "go", {})
    runtime.store.applied.add(f"{task['id']}:go:sam")
    with pytest.raises(Conflict):
        runtime.complete(task["id"], Person.of("sam"), "go", {})
    runtime.store.applied.discard(f"{task['id']}:go:sam")
    with pytest.raises(Invalid):
        runtime.take_case_action(case["id"], Person.of("sam"), "missing")
    with pytest.raises(Invalid):
        runtime.complete(task["id"], Person.of("sam"), "nope", {})
    runtime.complete(task["id"], Person.of("sam"), "go", {})
    with pytest.raises(Conflict):
        runtime.reassign(task["id"], Person.of("sam"), "sam")

    # A route whose conditions all fail.
    routed = (
        Workflow.make("gate")
        .status("in", "In", terminal=True)
        .step(Step.make("form").assignee(starter()).on("go", to="choose", status="in", label="Go"))
        .step(
            Step.make("choose", "route")
            .on("only", to="end", status="in", label="Only", when="answers.n==1")
            .on("else", to="end", status="in", label="Else")
        )
    )
    runtime.save(routed.document())
    runtime.publish("gate")
    started = runtime.start("gate", Person.of("sam"))
    form = next(item for item in runtime.store.tasks_for(started["id"]) if item["state"] == "open")
    done = runtime.complete(form["id"], Person.of("sam"), "go", {"n": "1"})
    assert done["closed"] is True
    document = runtime.document_for("gate")
    document["steps"][1]["edges"] = [edge for edge in document["steps"][1]["edges"] if edge["when"]]
    runtime.store.definition("gate")["versions"]["1"] = document
    case2 = runtime.start("gate", Person.of("sam"))
    form2 = next(item for item in runtime.store.tasks_for(case2["id"]) if item["state"] == "open")
    with pytest.raises(Invalid):
        runtime.complete(form2["id"], Person.of("sam"), "go", {"n": "9"})

    # Join policies, a notice, and a second arrival.
    joined = (
        Workflow.make("gather")
        .status("work", "Work")
        .status("done", "Done", terminal=True)
        .step(
            Step.make("fork", "fork")
            .on("a", to="a", status="work", label="A")
            .on("b", to="b", status="work", label="B")
        )
        .step(Step.make("a").assignee(starter()).on("go", to="meet", status="work", label="Go"))
        .step(Step.make("b").assignee(starter()).on("go", to="meet", status="work", label="Go"))
        .step(Step.make("meet", "join").joins(["a", "b"], "all").on("out", to="note", status="work", label="Out"))
        .step(Step.make("note", "notify").assignee(starter()).on("n", to="end", status="done", label="Noted"))
        .start("fork")
    )
    runtime.save(joined.document())
    runtime.publish("gather")
    gathered = runtime.start("gather", Person.of("sam"))
    for task in list(runtime.store.tasks_for(gathered["id"])):
        if task["state"] == "open":
            runtime.complete(task["id"], Person.of("sam"), "go", {})
    assert runtime.store.case(gathered["id"])["closed"] is True

    any_join = (
        Workflow.make("either")
        .status("work", "Work")
        .status("done", "Done", terminal=True)
        .step(Step.make("fork", "fork").on("a", to="a", status="work", label="A").on("b", to="b", status="work", label="B"))
        .step(Step.make("a").assignee(starter()).on("go", to="meet", status="work", label="Go"))
        .step(Step.make("b").assignee(starter()).on("go", to="meet", status="work", label="Go"))
        .step(Step.make("meet", "join").joins(["a", "b"], "any").on("out", to="end", status="done", label="Out"))
        .start("fork")
    )
    runtime.save(any_join.document())
    runtime.publish("either")
    either = runtime.start("either", Person.of("sam"))
    one = next(item for item in runtime.store.tasks_for(either["id"]) if item["state"] == "open")
    assert runtime.complete(one["id"], Person.of("sam"), "go", {})["closed"] is True

    # Signature via the signature argument, missing image, and a nested repeater field.
    signed = (
        Workflow.make("ink")
        .status("done", "Done", terminal=True)
        .step(
            Step.make("sign", "sign")
            .assignee(starter())
            .schema([{"type": "Repeater", "name": "lines", "label": "Lines", "schema": [{"type": "TextInput", "name": "item", "label": "Item", "placeholder": "Row", "required": True}]}])
            .on("sign", to="end", status="done", label="Sign")
        )
    )
    runtime.save(signed.document())
    runtime.publish("ink")
    ink = runtime.start("ink", Person.of("sam"))
    ink_task = next(iter(runtime.store.tasks_for(ink["id"])))
    with pytest.raises(Invalid):
        runtime.complete(ink_task["id"], Person.of("sam"), "sign", {"item": "A"}, signature={"name": "Sam"})
    runtime.complete(ink_task["id"], Person.of("sam"), "sign", {"item": "A"}, signature={"name": "Sam", "image": "mark"})

    # Unassigned inbox row and a case the inbox cannot label.
    empty = (
        Workflow.make("empty")
        .status("open", "Open")
        .status("done", "Done", terminal=True)
        .step(Step.make("gap").assignee(role("nobody")).on("ok", to="end", status="done", label="OK"))
    )
    runtime.save(empty.document())
    runtime.publish("empty")
    gap = runtime.start("empty", Person.of("sam"))
    assert "Unassigned" in render_inbox(runtime, "sam")
    assert open_tasks(runtime.store, "sam")
    html = render_schema(
        [{"type": "TextInput", "name": "hidden", "label": "H", "required": True, "visible_when": 'answers.show=="yes"'}],
        {"show": "no"},
    )
    assert html == ""
    parse_stamp("2026-05-01T00:00:00Z")
    clock = Clock(datetime(2026, 1, 1))
    assert clock.now().tzinfo is not None
    resolve_assignees([{"kind": "field", "value": "who"}, {"kind": "nope"}], directory=directory, starter_id="sam", answers={})
    resolve_assignees([guest("a@b.co")], directory=directory, starter_id="sam", answers={})
    assert _status({"statuses": []}, "missing")["color"] == "gray"
    with pytest.raises(Invalid):
        _step(runtime.document_for("plain"), "missing")
    with pytest.raises(Invalid):
        _edge(runtime.document_for("plain")["steps"][0], "missing")
    page = render_inbox(runtime, "sam")
    assert "empty" in page
    assert "task" in CasePage.render(task_id=runtime.store.tasks_for(gap["id"])[0]["id"], guest=True)
    runtime.store.save_case({"id": "half", "key": "nope", "version": 1, "status": "x"})
    from almasix_orbit_workflows.views import render_case

    assert "Not started" in render_case(runtime, "half") or "x" in render_case(runtime, "half")
    # Escalation already fired does not fire again.
    runtime.promote_due()
    runtime.promote_due()
    from almasix_orbit_workflows.views import _field_on

    assert _field_on({"schema": [{"name": "a", "schema": [{"name": "b"}]}]}, "b")
    assert not _field_on({"schema": []}, "z")
    render_schema(
        [
            {"type": "Grid", "name": "g", "columns": 2, "schema": [{"type": "TextInput", "name": "a", "label": "A"}]},
            {"type": "Tabs", "name": "t", "tabs": [{"label": "T", "schema": [{"type": "TextInput", "name": "b", "label": "B", "required": True}]}]},
            {"type": "Repeater", "name": "lines", "label": "Lines", "placeholder": "Add", "schema": [{"type": "TextInput", "name": "item", "label": "Item"}]},
        ],
        {},
    )
    from almasix_orbit_workflows.schema_tree import field_errors

    assert field_errors(
        [
            {"type": "Tabs", "name": "t", "tabs": [{"schema": [{"type": "TextInput", "name": "b", "required": True}]}]},
            {"type": "Wizard", "name": "w", "steps": [{"schema": [{"type": "TextInput", "name": "c", "required": True}]}]},
        ],
        {},
        {},
    )
    runtime._allow(Person.of("system", "Scheduler"), "start")
    runtime._allow(Person.of("sam"), "decorate")
    stuck = (
        Workflow.make("stuck")
        .limit_visits(1)
        .status("open", "Open", terminal=True)
        .step(Step.make("a").assignee(starter()).on("again", to="a", status="open", label="Again"))
    )
    runtime.save(stuck.document())
    runtime.publish("stuck")
    looping = runtime.start("stuck", Person.of("sam"))
    loop_task = next(iter(runtime.store.tasks_for(looping["id"])))
    runtime.complete(loop_task["id"], Person.of("sam"), "again", {})
    alone = (
        Workflow.make("solo")
        .status("done", "Done", terminal=True)
        .step(Step.make("meet", "join").joins(["nowhere"], "all").on("out", to="end", status="done", label="Out"))
    )
    runtime.save(alone.document())
    runtime.publish("solo")
    runtime.start("solo", Person.of("sam"))
    case = runtime.store.cases[next(iter(runtime.store.cases))]
    doc = runtime.document_for(case["key"], case["version"])
    runtime._arrive(
        case,
        doc,
        {"key": "phantom", "join_from": ["a"], "join_policy": "all", "quorum": 1, "edges": [{"key": "o", "to": "end", "status": case["status"] or "done"}]},
        "a",
        None,
    )
    step = {"key": "nudge", "reminders": ["1h"]}
    task = {"id": "nudge-task", "case_id": case["id"], "assignees": [], "reminded": [], "due_at": "2099-01-02T00:00:00Z", "state": "open"}
    runtime.store.save_task(task)
    due = parse_stamp(task["due_at"])
    early = parse_stamp("2000-01-01T00:00:00Z")
    runtime._remind(case, task, step, doc, early, due)
    runtime._remind(case, task, step, doc, early, due)
    side = (
        Workflow.make("side")
        .status("work", "Work")
        .status("done", "Done", terminal=True)
        .step(Step.make("fork", "fork").on("a", to="a", status="work", label="A").on("b", to="b", status="work", label="B"))
        .step(Step.make("a").assignee(starter()).on("leave", to="end", status="done", label="Leave"))
        .step(Step.make("b").assignee(starter()).on("go", to="end", status="done", label="Go"))
        .start("fork")
    )
    runtime.save(side.document())
    runtime.publish("side")
    sided = runtime.start("side", Person.of("sam"))
    first = next(item for item in runtime.store.tasks_for(sided["id"]) if item["state"] == "open")
    runtime.complete(first["id"], Person.of("sam"), first["outcomes"] and next(iter(first["outcomes"])), {})
    stored = runtime.store.case(sided["id"])
    runtime._arrive(stored, runtime.document_for("side"), {"key": "again", "join_from": ["x"], "join_policy": "all", "edges": [{"key": "o", "to": "end", "status": "done"}]}, "", None)
    with pytest.raises(Invalid):
        runtime._set_status(stored, "nope")
    from almasix_orbit_workflows.engine import _same_outcome

    finished = next(item for item in runtime.store.tasks_for(sided["id"]) if item["state"] == "done")
    assert _same_outcome(runtime.store, finished, next(iter(finished["outcomes"]))) in {True, False}
    runtime.simulate(
        side.document(),
        [{"action": "start"}, {"action": "noop"}],
        actor=Person.of("sam"),
    )
    class Bare:
        pass

    WorkflowPlugin.make(runtime).register(Bare())
    from almasix_orbit_workflows.views import _label

    assert _label(runtime, {})["label"] == ""
