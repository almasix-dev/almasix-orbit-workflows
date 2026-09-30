"""Branches the main story does not pass through: validation, schema hydration, and the clock."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from almasix_orbit_workflows import (
    Clock,
    Directory,
    Person,
    Step,
    Workflow,
    WorkflowEngine,
    expression,
    guest,
    role,
    starter,
    user,
)
from almasix_orbit_workflows import plugin as plugin_mod
from almasix_orbit_workflows.clock import (
    add_duration,
    parse_duration,
    parse_stamp,
    stamp,
    subtract_duration,
)
from almasix_orbit_workflows.design import connect
from almasix_orbit_workflows.document import check_document
from almasix_orbit_workflows.engine import _same_outcome
from almasix_orbit_workflows.errors import Conflict, Forbidden, Invalid, NotFound, Refused
from almasix_orbit_workflows.plugin import CanvasPage, WorkflowPlugin, current_engine
from almasix_orbit_workflows.predicates import matches
from almasix_orbit_workflows.schema_tree import SignatureInput, hydrate, render_schema
from almasix_orbit_workflows.views import render_inbox, render_task


def _base() -> dict:
    return (
        Workflow.make("tiny")
        .status("go", "Go", terminal=True)
        .step(Step.make("only").assignee(starter()).on("ok", to="end", status="go", label="OK"))
        .document()
    )


def test_validation_messages_cover_a_bad_document() -> None:
    good = _base()
    good["steps"].append(dict(good["steps"][0]))
    errors = " ".join(check_document({}))
    assert "status" in errors and "step" in errors
    broken = _base()
    broken["start"] = "missing"
    broken["timezone"] = "Mars/Base"
    broken["stuck_status"] = "nope"
    broken["case_actions"] = [{"key": "x", "status": "nope", "label": "X", "who": "starter"}]
    broken["steps"][0]["kind"] = "nope"
    text = " ".join(check_document(broken))
    assert "unknown kind" in text
    assert "twice" in " ".join(check_document(good))
    route = _base()
    route["steps"][0]["kind"] = "route"
    route["steps"][0]["policy"] = "sometimes"
    route["steps"][0]["edges"] = [
        {
            "key": "a",
            "to": "missing",
            "status": "missing",
            "label": "A",
            "when": "answers.a==1",
            "branch": "sideways",
        }
    ]
    route["steps"][0]["assignees"] = [{"kind": "nope"}]
    route["steps"][0]["escalate"] = {"after": "1d", "to": "nowhere", "status": "missing"}
    listed = " ".join(check_document(route))
    assert "default edge" in listed
    assert "policy" in listed
    notice = _base()
    notice["steps"][0]["kind"] = "notify"
    notice["steps"][0]["edges"] = []
    assert "exactly one" in " ".join(check_document(notice))
    join = _base()
    join["steps"][0]["kind"] = "join"
    join["steps"][0]["join_from"] = []
    join["steps"][0]["join_policy"] = "nope"
    assert "waits for" in " ".join(check_document(join))
    quorum = _base()
    quorum["steps"][0]["policy"] = "quorum"
    quorum["steps"][0]["quorum"] = 0
    assert "quorum" in " ".join(check_document(quorum))
    hidden = _base()
    hidden["steps"].append(
        {
            "key": "side",
            "kind": "form",
            "assignees": [],
            "policy": "any",
            "quorum": 1,
            "schema": [],
            "edges": [],
            "join_from": [],
            "join_policy": "all",
        }
    )
    assert "cannot be reached" in " ".join(check_document(hidden))


def test_schema_renders_layouts_and_rejects_unknown_types() -> None:
    tree = [
        {
            "type": "Wizard",
            "name": "pages",
            "steps": [
                {
                    "label": "One",
                    "schema": [
                        {
                            "type": "Tabs",
                            "name": "tabs",
                            "tabs": [
                                {
                                    "label": "Main",
                                    "schema": [
                                        {
                                            "type": "Select",
                                            "name": "choice",
                                            "label": "Choice",
                                            "options": {"a": "A"},
                                            "required": True,
                                        },
                                        {"type": "Checkbox", "name": "ok", "label": "OK"},
                                        {"type": "Text", "name": "blurb", "content": "Read this"},
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        },
        {"type": "Callout", "name": "note", "heading": "Note"},
        {"type": "SignatureInput", "name": "signature", "label": "Sign here"},
    ]
    html = render_schema(tree, {"signature": "ink", "choice": "a"})
    assert "or-field" in html
    assert SignatureInput.make("signature").label("Sign").required().get_state_path() == "signature"
    with pytest.raises(Invalid):
        hydrate({"type": "NotAField", "name": "x"})


def test_clock_units_and_live_now() -> None:
    assert Clock().now().tzinfo is not None
    fixed = Clock(datetime(2026, 1, 1, tzinfo=UTC))
    assert parse_duration("4h")[0] == "seconds"
    assert parse_duration("30m")[1] == 1800
    later = add_duration(fixed.now(), "2h", "UTC")
    assert later > fixed.now()
    assert subtract_duration(later, "2h", "UTC") == fixed.now()
    parsed = parse_stamp("2026-01-01T00:00:00")
    assert stamp(parsed).endswith("Z")
    with pytest.raises(Invalid):
        parse_duration("nope")
    with pytest.raises(Invalid):
        parse_duration("2x")


def test_predicates_cover_each_operator() -> None:
    assert matches("answers.n>1", {"n": 2}, {}, {})
    assert matches("answers.n<5", {"n": 2}, {}, {})
    assert matches("answers.n<=2", {"n": 2}, {}, {})
    assert not matches("answers.n!=2", {"n": "2"}, {}, {})
    assert matches("answers.tag=='a'", {"tag": "a"}, {}, {})
    assert matches("answers.gone==null", {}, {}, {})
    assert matches("answers.flag==false", {"flag": False}, {}, {})
    assert matches("answers.n==1.5", {"n": 1.5}, {}, {})
    assert matches("effect:ok", {}, {"ok": lambda answers, context: True}, {})
    with pytest.raises(Invalid):
        matches("effect:missing", {}, {}, {})
    with pytest.raises(Invalid):
        matches("answers.flag", {"flag": True}, {}, {})
    with pytest.raises(Invalid):
        matches("answers.n>1", {"n": "x"}, {}, {})


def test_directory_edges_and_notify_and_quorum() -> None:
    directory = Directory()
    directory.replace(
        [
            {"id": "sam", "name": "Sam", "roles": ["sales"], "manager_id": "boss"},
            {"id": "boss", "roles": ["lead"]},
        ]
    )
    assert directory.get(None) is None
    assert directory.get("missing") is None
    assert directory.manager_of("sam").id == "boss"
    assert directory.manager_of("boss") is None
    assert directory.has_role("nope", "lead") is False
    runtime = WorkflowEngine(directory=directory, clock=Clock())
    flow = (
        Workflow.make("notes")
        .status("sent", "Sent", terminal=True)
        .status("mid", "Mid")
        .step(
            Step.make("ping", "notify")
            .assignee(user("boss"))
            .on("go", to="vote", status="mid", label="Pinged")
        )
        .step(
            Step.make("vote", "approval")
            .assignee(user("sam"))
            .assignee(user("boss"))
            .assignee(user("missing"))
            .assignee(expression("manager_of:starter"))
            .assignee(expression("not-a-rule"))
            .assignee(guest("", "Nobody"))
            .how_many("quorum", 1)
            .on("ok", to="end", status="sent", label="OK")
        )
        .start("ping")
    )
    runtime.save(flow.document())
    runtime.publish("notes")
    case = runtime.start("notes", Person.of("sam", "Sam"))
    task = next(item for item in runtime.store.tasks_for(case["id"]) if item["step"] == "vote")
    assert runtime.complete(task["id"], Person.of("sam"), "ok", {})["closed"] is True
    with pytest.raises(Conflict):
        runtime.claim(task["id"], Person.of("sam"))
    with pytest.raises(NotFound):
        runtime.save_draft("missing", Person.of("sam"), {})
    with pytest.raises(Forbidden):
        runtime.comment(case["id"], Person(id="", label=""), "no")
    with pytest.raises(Conflict):
        runtime.take_case_action(case["id"], Person.of("sam"), "nope")
    other = runtime.store.tasks_for(case["id"])[0]
    with pytest.raises(Conflict):
        runtime.release(other["id"], Person.of("sam"))
    assert _same_outcome(runtime.store, {"id": "none", "case_id": case["id"]}, "ok") is False


def test_effect_errors_release_and_simulation() -> None:
    runtime = WorkflowEngine(
        directory=Directory([{"id": "sam", "name": "Sam", "roles": ["operate"]}]), clock=Clock()
    )

    def boom(answers, context):
        del answers, context
        raise ValueError("ledger down")

    runtime.effects["boom"] = boom
    flow = (
        Workflow.make("money")
        .status("done", "Done", terminal=True)
        .case_action("stop", status="done", label="Stop", who="role:operate")
        .step(
            Step.make("pay")
            .assignee(starter())
            .on("go", to="end", status="done", label="Pay", effect="boom")
        )
    )
    runtime.save(flow.document())
    runtime.publish("money")
    case = runtime.start("money", Person.of("sam"))
    task = next(iter(runtime.store.tasks_for(case["id"])))
    with pytest.raises(Refused):
        runtime.complete(task["id"], Person.of("sam"), "go", {})
    runtime.take_case_action(case["id"], Person.of("sam"), "stop")
    with pytest.raises(Conflict):
        runtime.take_case_action(case["id"], Person.of("sam"), "stop")
    fresh = runtime.start("money", Person.of("sam"))
    held = next(item for item in runtime.store.tasks_for(fresh["id"]) if item["state"] == "open")
    runtime.claim(held["id"], Person.of("sam"))
    with pytest.raises(Forbidden):
        runtime.release(held["id"], Person.of("nope-but"))
    runtime.directory.replace([{"id": "sam", "roles": []}, {"id": "ava", "roles": ["operate"]}])
    held["assignees"].append({"id": "ava", "label": "Ava", "guest": False, "email": None})
    with pytest.raises(Conflict):
        runtime.claim(held["id"], Person.of("ava"))
    with pytest.raises(Forbidden):
        runtime.release(held["id"], Person.of("ava"))
    preview = runtime.simulate(
        Workflow.make("money2")
        .status("done", "Done", terminal=True)
        .step(Step.make("pay").assignee(starter()).on("go", to="end", status="done", label="Pay"))
        .document(),
        [
            {"action": "start"},
            {"action": "complete", "outcome": "go", "payload": {}},
            {"action": "advance", "delta": {"hours": 1}},
        ],
        actor=Person.of("sam"),
    )
    assert preview["closed"] is True
    with pytest.raises(Invalid):
        runtime.simulate(flow.document(), [], actor=Person.of("sam"))
    with pytest.raises(NotFound):
        runtime.simulate(
            flow.document(),
            [{"action": "start"}, {"action": "complete", "outcome": "go", "step": "missing"}],
            actor=Person.of("sam"),
        )
    with pytest.raises(NotFound):
        runtime.retire("missing-too")
    with pytest.raises(NotFound):
        WorkflowEngine().start("money", Person.of("sam"))
    runtime.retire("money")
    assert runtime.start("money", Person.of("sam"), allow_retired=True)["key"] == "money"


def test_pages_render_the_remaining_states() -> None:
    runtime = WorkflowEngine(
        directory=Directory([{"id": "sam", "name": "Sam", "roles": ["sales"]}]), clock=Clock()
    )
    flow = (
        Workflow.make("box")
        .status("open", "Open")
        .status("done", "Done", terminal=True)
        .step(
            Step.make("work", "approval")
            .assignee(role("sales"))
            .on("ok", to="end", status="done", label="OK")
        )
    )
    runtime.save(flow.document())
    runtime.publish("box")
    case = runtime.start("box", Person.of("sam"))
    task = next(iter(runtime.store.tasks_for(case["id"])))
    runtime.claim(task["id"], Person.of("sam"))
    html = render_inbox(runtime, "sam")
    assert "Claimed" in html
    guest_view = render_task(runtime, task["id"], guest=True)
    assert "OK" in guest_view
    plugin_mod._ENGINE = None
    assert current_engine() is not None
    plugin = WorkflowPlugin.make(runtime)
    panel = type("P", (), {"pages": lambda self, pages: setattr(self, "got", pages), "got": None})()
    plugin.register(panel)
    plugin.boot(panel)
    assert "wf-canvas" in CanvasPage.render(key="missing")
    assert "Timeline" in __import__(
        "almasix_orbit_workflows.views", fromlist=["render_case"]
    ).render_case(runtime, case["id"])
    with pytest.raises(KeyError):
        connect({"steps": []}, "nope", "x", to="end", status="done", label="X")
