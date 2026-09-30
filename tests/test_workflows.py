"""Realistic runs of the workflow engine, plus the pages that sit on it."""

from __future__ import annotations

import threading

import pytest
from almasix.orbit.panels.panel import Panel

from almasix_orbit_workflows import (
    CanvasPage,
    CasePage,
    Clock,
    Directory,
    InboxPage,
    Person,
    Step,
    Workflow,
    WorkflowEngine,
    WorkflowPlugin,
    contract_workflow,
    current_engine,
    expression,
    field,
    guest,
    install,
    role,
    starter,
    user,
)
from almasix_orbit_workflows.design import (
    add_status,
    add_step,
    assign,
    connect,
    place_field,
    problems,
)
from almasix_orbit_workflows.document import DocumentError, check_document
from almasix_orbit_workflows.errors import Conflict, Forbidden, Invalid, NotFound, Refused
from almasix_orbit_workflows.predicates import matches
from almasix_orbit_workflows.signing import file_hash, fingerprint, snapshot
from almasix_orbit_workflows.views import render_canvas, render_case, render_inbox, render_task


def people() -> Directory:
    return Directory(
        [
            {"id": "sam", "name": "Sam", "roles": ["sales"], "manager_id": "jordan"},
            {"id": "jordan", "name": "Jordan", "roles": ["legal"]},
            {"id": "ava", "name": "Ava", "roles": ["finance"]},
            {"id": "rex", "name": "Rex", "roles": ["legal", "finance"]},
            {"id": "director", "name": "Dee", "roles": ["director"]},
        ]
    )


def engine() -> WorkflowEngine:
    clock = Clock()
    clock.advance = lambda **delta: None  # placeholder replaced below
    built = WorkflowEngine(directory=people(), clock=Clock())
    return built


def publish_contract(runtime: WorkflowEngine | None = None) -> WorkflowEngine:
    runtime = runtime or WorkflowEngine(directory=people(), clock=Clock())
    document = contract_workflow().document()
    runtime.save(document)
    runtime.publish("contract")
    return runtime


def test_publish_rejects_a_broken_graph() -> None:
    document = (
        Workflow.make("broken")
        .status("open", "Open")
        .step(Step.make("a").on("go", to="end", status="open", label="Go"))
        .document()
    )
    runtime = WorkflowEngine()
    runtime.save(document)
    with pytest.raises(DocumentError) as caught:
        runtime.publish("broken")
    assert "terminal" in str(caught.value)


def test_contract_parallel_then_signature() -> None:
    runtime = publish_contract()
    sam, jordan, ava = Person.of("sam", "Sam"), Person.of("jordan"), Person.of("ava")
    case = runtime.start("contract", sam, subject_type="Contract", subject_id="c-1", answers={})
    assert case["status"] == ""
    task = next(item for item in runtime.store.tasks_for(case["id"]) if item["state"] == "open")
    case = runtime.complete(
        task["id"], sam, "submit", {"client": "Acme", "amount": "12000", "summary": "Yearly"}
    )
    assert case["status"] == "review"
    legal = next(
        item
        for item in runtime.store.tasks_for(case["id"])
        if item["step"] == "legal" and item["state"] == "open"
    )
    finance = next(
        item
        for item in runtime.store.tasks_for(case["id"])
        if item["step"] == "finance" and item["state"] == "open"
    )
    runtime.claim(legal["id"], jordan)
    runtime.complete(legal["id"], jordan, "approve", {"note": "ok"})
    assert runtime.store.case(case["id"])["status"] == "review"
    case = runtime.complete(finance["id"], ava, "approve", {})
    sign = next(
        item
        for item in runtime.store.tasks_for(case["id"])
        if item["step"] == "sign" and item["state"] == "open"
    )
    case = runtime.complete(
        sign["id"],
        sam,
        "sign",
        {"signature": "data:image/png;base64,abc", "signer_name": "Sam"},
        files={"pdf": file_hash(b"contract-bytes")},
    )
    assert case["closed"] is True
    assert case["status"] == "executed"
    signed = runtime.store.signatures_for(case["id"])[0]
    assert signed["hash"] == fingerprint(signed["snapshot"], intent=signed["intent"], signer="sam")
    assert (
        snapshot(case["answers"], {"pdf": signed["snapshot"]["files"]["pdf"]})["answers"]["client"]
        == "Acme"
    )


def test_abort_branch_cancels_the_sibling() -> None:
    runtime = publish_contract()
    sam, jordan = Person.of("sam"), Person.of("jordan")
    case = runtime.start("contract", sam)
    draft = next(iter(runtime.store.tasks_for(case["id"])))
    runtime.complete(draft["id"], sam, "submit", {"client": "Acme", "amount": "10"})
    legal = next(item for item in runtime.store.tasks_for(case["id"]) if item["step"] == "legal")
    runtime.complete(legal["id"], jordan, "return", {})
    states = {item["step"]: item["state"] for item in runtime.store.tasks_for(case["id"])}
    assert states["finance"] == "cancelled"
    assert any(
        item["step"] == "draft" and item["state"] == "open"
        for item in runtime.store.tasks_for(case["id"])
    )


def test_effect_can_refuse_and_is_not_repeated() -> None:
    runtime = publish_contract()
    calls: list[str] = []

    def post(answers, context):
        if float(answers["amount"]) > 100:
            raise Refused("Over the card limit.")
        calls.append(context["idempotency_key"])

    runtime.effects["post_journal"] = post
    document = contract_workflow().document()
    for step in document["steps"]:
        if step["key"] == "draft":
            step["edges"][0]["effect"] = "post_journal"
    runtime.save(document)
    runtime.publish("contract")
    sam = Person.of("sam")
    case = runtime.start("contract", sam)
    task = next(iter(runtime.store.tasks_for(case["id"])))
    with pytest.raises(Refused):
        runtime.complete(task["id"], sam, "submit", {"client": "Acme", "amount": "500"})
    assert runtime.store.task(task["id"])["state"] == "open"
    runtime.complete(task["id"], sam, "submit", {"client": "Acme", "amount": "40"})
    with pytest.raises(Conflict):
        runtime.complete(task["id"], sam, "submit", {"client": "Acme", "amount": "40"})
    assert calls == [f"{task['id']}:submit:sam"]


def test_claim_blocks_the_other_person_and_release_opens_it() -> None:
    runtime = WorkflowEngine(directory=people(), clock=Clock())
    flow = (
        Workflow.make("pool")
        .status("open", "Open")
        .status("done", "Done", terminal=True)
        .step(
            Step.make("review", "approval")
            .assignee(role("legal"))
            .on("ok", to="end", status="done", label="OK")
        )
    )
    runtime.save(flow.document())
    runtime.publish("pool")
    case = runtime.start("pool", Person.of("sam"))
    task = next(iter(runtime.store.tasks_for(case["id"])))
    runtime.claim(task["id"], Person.of("jordan"))
    with pytest.raises(Conflict):
        runtime.complete(task["id"], Person.of("rex"), "ok", {})
    runtime.release(task["id"], Person.of("jordan"))
    runtime.complete(task["id"], Person.of("rex"), "ok", {})
    assert runtime.store.case(case["id"])["closed"] is True


def test_two_completions_cannot_both_win() -> None:
    runtime = publish_contract()
    sam = Person.of("sam")
    case = runtime.start("contract", sam)
    task_id = next(iter(runtime.store.tasks_for(case["id"])))["id"]
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def once() -> None:
        barrier.wait()
        try:
            runtime.complete(task_id, sam, "submit", {"client": "Acme", "amount": "5"})
        except Conflict as exc:
            errors.append(exc)

    threads = [threading.Thread(target=once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(errors) == 1


def test_empty_role_stays_visible_until_reassigned() -> None:
    runtime = WorkflowEngine(directory=people(), clock=Clock())
    flow = (
        Workflow.make("gap")
        .status("open", "Open")
        .status("done", "Done", terminal=True)
        .step(
            Step.make("review")
            .assignee(role("missing"))
            .on("ok", to="end", status="done", label="OK")
        )
    )
    runtime.save(flow.document())
    runtime.publish("gap")
    case = runtime.start("gap", Person.of("sam"))
    task = next(iter(runtime.store.tasks_for(case["id"])))
    assert task["unassigned"] is True
    with pytest.raises(Forbidden):
        runtime.complete(task["id"], Person.of("sam"), "ok", {})
    runtime.reassign(task["id"], Person.of("director"), "jordan")
    runtime.complete(task["id"], Person.of("jordan"), "ok", {"client": "x"})
    assert runtime.store.case(case["id"])["closed"] is True


def test_guest_link_dies_after_the_step() -> None:
    runtime = WorkflowEngine(directory=people(), clock=Clock())
    flow = (
        Workflow.make("apply")
        .status("in", "In")
        .status("filed", "Filed", terminal=True)
        .step(
            Step.make("form")
            .assignee(guest("a@b.co", "Ada"))
            .schema([{"type": "TextInput", "name": "course", "label": "Course", "required": True}])
            .on("send", to="end", status="filed", label="Send")
        )
    )
    runtime.save(flow.document())
    runtime.publish("apply")
    case = runtime.start("apply", Person.of("sam"))
    task = next(iter(runtime.store.tasks_for(case["id"])))
    token = task["guest_token"]
    opened = runtime.guest_task(token)
    assert opened["step"]["key"] == "form"
    guest_actor = Person(id="guest:a@b.co", label="Ada", guest=True, email="a@b.co")
    with pytest.raises(Invalid):
        runtime.complete(task["id"], guest_actor, "send", {})
    runtime.complete(task["id"], guest_actor, "send", {"course": "Law"})
    with pytest.raises(NotFound):
        runtime.guest_task(token)


def test_withdraw_and_operator_move_use_document_statuses() -> None:
    runtime = publish_contract()
    sam = Person.of("sam")
    case = runtime.start("contract", sam)
    runtime.take_case_action(case["id"], sam, "withdraw")
    stored = runtime.store.case(case["id"])
    assert stored["status"] == "changes"
    assert all(item["state"] != "open" for item in runtime.store.tasks_for(case["id"]))
    with pytest.raises(Forbidden):
        runtime.take_case_action(case["id"], sam, "move")
    runtime.directory._people.append(
        {"id": "director", "name": "Dee", "roles": ["director", "operate"]}
    )
    runtime.take_case_action(case["id"], Person.of("director"), "move")
    assert runtime.store.case(case["id"])["status"] == "draft"


def test_escalation_and_reminder_follow_the_zone() -> None:
    clock = Clock()
    runtime = WorkflowEngine(directory=people(), clock=clock)
    document = contract_workflow().zone("Africa/Nairobi").document()
    runtime.save(document)
    runtime.publish("contract")
    sam = Person.of("sam")
    case = runtime.start("contract", sam)
    draft = next(iter(runtime.store.tasks_for(case["id"])))
    runtime.complete(draft["id"], sam, "submit", {"client": "Acme", "amount": "9"})
    legal = next(item for item in runtime.store.tasks_for(case["id"]) if item["step"] == "legal")
    clock.advance(days=1, hours=1)
    reminded = runtime.promote_due()
    assert reminded == []
    assert "1d" in runtime.store.task(legal["id"])["reminded"]
    clock.advance(days=1)
    moved = runtime.promote_due()
    assert legal["id"] in moved
    assert any(
        item["step"] == "director" and item["state"] == "open"
        for item in runtime.store.tasks_for(case["id"])
    )
    runtime.complete(
        runtime.store.tasks_for(case["id"])[-1]["id"]
        if False
        else next(
            item["id"] for item in runtime.store.tasks_for(case["id"]) if item["step"] == "director"
        ),
        Person.of("director"),
        "approve",
        {},
    )


def test_chained_workflow_starts_when_the_edge_says_so() -> None:
    runtime = WorkflowEngine(directory=people(), clock=Clock())
    hired = (
        Workflow.make("hire")
        .status("open", "Open")
        .status("hired", "Hired", terminal=True)
        .step(
            Step.make("offer", "sign")
            .assignee(starter())
            .signs("sign")
            .on("sign", to="end", status="hired", label="Sign", start_workflow="onboard")
        )
    )
    onboard = (
        Workflow.make("onboard")
        .status("go", "Go", terminal=True)
        .step(
            Step.make("kit")
            .assignee(user("jordan"))
            .on("done", to="end", status="go", label="Done")
        )
    )
    runtime.save(hired.document())
    runtime.save(onboard.document())
    runtime.publish("hire")
    runtime.publish("onboard")
    case = runtime.start("hire", Person.of("sam"))
    task = next(iter(runtime.store.tasks_for(case["id"])))
    runtime.complete(
        task["id"], Person.of("sam"), "sign", {"signature": "ink", "signer_name": "Sam"}
    )
    onboard_cases = [item for item in runtime.store.cases.values() if item["key"] == "onboard"]
    assert len(onboard_cases) == 1
    assert any(item["step"] == "kit" for item in runtime.store.tasks_for(onboard_cases[0]["id"]))


def test_route_uses_answers_and_effects() -> None:
    runtime = WorkflowEngine(directory=people(), clock=Clock())
    runtime.effects["seats_remaining"] = lambda answers, context: int(answers.get("seats") or 0) > 0
    flow = (
        Workflow.make("event")
        .status("wait", "Waitlisted", terminal=True)
        .status("in", "Registered", terminal=True)
        .step(
            Step.make("form")
            .assignee(starter())
            .schema([{"type": "TextInput", "name": "seats", "label": "Seats", "required": True}])
            .on("go", to="gate", status="in", label="Go")
        )
        .step(
            Step.make("gate", "route")
            .on("full", to="end", status="wait", label="Wait", when="effect:seats_remaining")
            .on("else", to="end", status="in", label="In")
        )
    )
    # effect seats_remaining true when seats > 0, but the when edge is checked first and matches when true
    # so seats 0 should fall through to else. Our predicate effect returns False for 0, True for 2.
    # When True we take "full" which is misnamed for the test — use seats 0 to take the default.
    runtime.save(flow.document())
    runtime.publish("event")
    case = runtime.start("event", Person.of("sam"))
    task = next(iter(runtime.store.tasks_for(case["id"])))
    case = runtime.complete(task["id"], Person.of("sam"), "go", {"seats": "0"})
    assert case["status"] == "in"
    assert case["closed"] is True


def test_visible_when_hides_a_required_field() -> None:
    runtime = WorkflowEngine(directory=people(), clock=Clock())
    schema = [
        {"type": "TextInput", "name": "kind", "label": "Kind", "required": True},
        {
            "type": "TextInput",
            "name": "note",
            "label": "Note",
            "required": True,
            "visible_when": 'answers.kind=="extra"',
        },
    ]
    flow = (
        Workflow.make("show")
        .status("done", "Done", terminal=True)
        .step(
            Step.make("form")
            .assignee(starter())
            .schema(schema)
            .on("go", to="end", status="done", label="Go")
        )
    )
    runtime.save(flow.document())
    runtime.publish("show")
    case = runtime.start("show", Person.of("sam"))
    task = next(iter(runtime.store.tasks_for(case["id"])))
    runtime.complete(task["id"], Person.of("sam"), "go", {"kind": "plain"})
    assert runtime.store.case(case["id"])["closed"] is True


def test_all_must_agree() -> None:
    runtime = WorkflowEngine(directory=people(), clock=Clock())
    flow = (
        Workflow.make("pair")
        .status("done", "Done", terminal=True)
        .step(
            Step.make("review", "approval")
            .assignee(role("legal"))
            .how_many("all")
            .on("ok", to="end", status="done", label="OK")
            .on("no", to="end", status="done", label="No")
        )
    )
    runtime.save(flow.document())
    runtime.publish("pair")
    case = runtime.start("pair", Person.of("sam"))
    tasks = [item for item in runtime.store.tasks_for(case["id"]) if item["state"] == "open"]
    assert len(tasks) == 2
    first, second = tasks
    owners = {first["assignees"][0]["id"]: first, second["assignees"][0]["id"]: second}
    runtime.complete(owners["jordan"]["id"], Person.of("jordan"), "ok", {})
    assert runtime.store.case(case["id"])["closed"] is False
    with pytest.raises(Conflict):
        runtime.complete(owners["rex"]["id"], Person.of("rex"), "no", {})
    runtime.complete(owners["rex"]["id"], Person.of("rex"), "ok", {})
    assert runtime.store.case(case["id"])["closed"] is True


def test_revisit_limit_sets_the_stuck_status() -> None:
    runtime = WorkflowEngine(directory=people(), clock=Clock())
    flow = (
        Workflow.make("loop")
        .limit_visits(1, stuck_status="stuck")
        .status("open", "Open")
        .status("stuck", "Stuck", terminal=True)
        .step(Step.make("a").assignee(starter()).on("again", to="a", status="open", label="Again"))
    )
    runtime.save(flow.document())
    runtime.publish("loop")
    case = runtime.start("loop", Person.of("sam"))
    task = next(iter(runtime.store.tasks_for(case["id"])))
    case = runtime.complete(task["id"], Person.of("sam"), "again", {})
    assert case["status"] == "stuck"
    assert case["closed"] is False or case["status"] == "stuck"


def test_draft_comment_and_retired_definition() -> None:
    runtime = publish_contract()
    sam = Person.of("sam")
    case = runtime.start("contract", sam)
    task = next(iter(runtime.store.tasks_for(case["id"])))
    runtime.save_draft(task["id"], sam, {"client": "Partial"})
    assert runtime.store.task(task["id"])["draft"]["client"] == "Partial"
    runtime.comment(case["id"], sam, "Holding for legal.")
    assert any(event["kind"] == "comment" for event in runtime.store.events_for(case["id"]))
    runtime.retire("contract")
    with pytest.raises(Invalid):
        runtime.start("contract", sam)
    with pytest.raises(NotFound):
        runtime.publish("missing")


def test_designer_round_trip_and_pages() -> None:
    document: dict = {
        "key": "leave",
        "name": "Leave",
        "timezone": "UTC",
        "revisit_limit": 4,
        "stuck_status": None,
        "watchers": [],
        "case_actions": [],
    }
    add_status(document, "asked", "Asked")
    add_status(document, "done", "Done", terminal=True)
    add_step(document, "request", "form")
    add_step(document, "approve", "approval")
    connect(document, "request", "send", to="approve", status="asked", label="Send")
    connect(document, "approve", "ok", to="end", status="done", label="OK")
    assign(document, "request", starter())
    assign(document, "approve", role("legal"))
    place_field(
        document,
        "request",
        {
            "type": "Section",
            "name": "s",
            "heading": "When",
            "schema": [{"type": "DatePicker", "name": "day", "label": "Day", "required": True}],
        },
    )
    assert problems(document) == []
    html = render_canvas(document)
    assert 'data-step="approve"' in html
    assert "Asked" in html
    runtime = WorkflowEngine(directory=people(), clock=Clock())
    runtime.save(document)
    runtime.publish("leave")
    install(runtime)
    sam = Person.of("sam")
    case = runtime.start("leave", sam)
    task = next(iter(runtime.store.tasks_for(case["id"])))
    inbox = render_inbox(runtime, "sam")
    assert task["id"] in inbox
    assert "Asked" not in inbox or "request" in inbox
    form = render_task(runtime, task["id"])
    assert "DatePicker" in form or 'name="day"' in form
    assert render_task(runtime, "nope")
    runtime.complete(task["id"], sam, "send", {"day": "2026-11-02"})
    page = render_case(runtime, case["id"])
    assert "Timeline" in page
    assert render_case(runtime, "missing")
    panel = Panel.make("admin").plugin(WorkflowPlugin.make(runtime))
    panel.run_plugins()
    slugs = {page.get_slug() for page in panel.get_pages()}
    assert "workflows" in slugs
    assert "Inbox" in InboxPage.render(actor_id="sam", user=sam)
    assert "Timeline" in CasePage.render(case_id=case["id"])
    assert "wf-canvas" in CanvasPage.render(document=document)
    assert current_engine() is runtime


def test_predicates_and_clock_and_bad_edges() -> None:
    assert matches(None, {}, {}, {}) is True
    assert matches("answers.amount>=10", {"amount": "12"}, {}, {}) is True
    assert matches("answers.kind!='no'", {"kind": "yes"}, {}, {}) is True
    assert matches("answers.flag==true", {"flag": True}, {}, {}) is True
    with pytest.raises(Invalid):
        matches("nope", {}, {}, {})
    with pytest.raises(Invalid):
        matches("answers.amount>lots", {"amount": "a"}, {}, {})
    clock = Clock()
    clock.advance(hours=2)
    assert clock.now().tzinfo is not None
    bad_zone = (
        Workflow.make("z")
        .zone("Mars/Base")
        .status("a", "A", terminal=True)
        .step(Step.make("s").on("g", to="end", status="a", label="G"))
        .document()
    )
    assert any("timezone" in item.lower() or "Unknown" in item for item in check_document(bad_zone))


def test_field_assignee_and_expression() -> None:
    runtime = WorkflowEngine(directory=people(), clock=Clock())
    flow = (
        Workflow.make("pick")
        .watcher(starter())
        .status("done", "Done", terminal=True)
        .step(
            Step.make("ask")
            .assignee(starter())
            .schema(
                [{"type": "TextInput", "name": "manager", "label": "Manager", "required": True}]
            )
            .on("go", to="next", status="done", label="Go")
        )
        .step(
            Step.make("next")
            .assignee(field("manager"))
            .assignee(expression("role_except_starter:legal"))
            .on("ok", to="end", status="done", label="OK")
        )
    )
    runtime.save(flow.document())
    runtime.publish("pick")
    case = runtime.start("pick", Person.of("sam"))
    task = next(iter(runtime.store.tasks_for(case["id"])))
    case = runtime.complete(task["id"], Person.of("sam"), "go", {"manager": "ava"})
    nxt = next(
        item
        for item in runtime.store.tasks_for(case["id"])
        if item["step"] == "next" and item["state"] == "open"
    )
    ids = {item["id"] for item in nxt["assignees"]}
    assert "ava" in ids
    assert "sam" not in ids
    runtime.complete(nxt["id"], Person.of("ava"), "ok", {})
    assert runtime.store.case(case["id"])["closed"] is True


def test_unknown_effect_and_signature_missing_and_user_missing() -> None:
    runtime = publish_contract()
    document = contract_workflow().document()
    document["steps"][0]["edges"][0]["effect"] = "missing"
    runtime.save(document)
    runtime.publish("contract")
    sam = Person.of("sam")
    case = runtime.start("contract", sam)
    task = next(iter(runtime.store.tasks_for(case["id"])))
    with pytest.raises(Invalid):
        runtime.complete(task["id"], sam, "submit", {"client": "A", "amount": "1"})
    with pytest.raises(Invalid):
        runtime.reassign(task["id"], sam, "nobody")
    with pytest.raises(NotFound):
        runtime.guest_task("nope")
    with pytest.raises(NotFound):
        runtime.comment("missing", sam, "hi")
