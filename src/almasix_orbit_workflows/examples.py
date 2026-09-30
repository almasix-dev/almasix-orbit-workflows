"""A contract workflow used by the sample and the tests. Statuses exist only here."""

from __future__ import annotations

from almasix_orbit_workflows.document import Step, Workflow, role, starter, user


def contract_workflow() -> Workflow:
    """Sales drafts, legal and finance review together, a quiet branch escalates, both sign."""
    draft = {
        "type": "Section",
        "name": "draft",
        "heading": "Contract",
        "schema": [
            {"type": "TextInput", "name": "client", "label": "Client", "required": True},
            {"type": "MoneyInput", "name": "amount", "label": "Amount", "required": True},
            {
                "type": "Grid",
                "name": "grid",
                "columns": 2,
                "schema": [
                    {"type": "Textarea", "name": "summary", "label": "Summary", "required": False},
                    {"type": "FileUpload", "name": "pdf", "label": "File", "required": False},
                ],
            },
        ],
    }
    flow = (
        Workflow.make("contract")
        .titled("Contract")
        .zone("UTC")
        .limit_visits(8, stuck_status="changes")
        .status("draft", "Draft", color="gray")
        .status("review", "In review", color="warning")
        .status("changes", "Changes needed", color="danger")
        .status("executed", "Executed", color="success", terminal=True)
        .case_action("withdraw", status="changes", label="Withdraw", who="starter")
        .case_action("move", status="draft", label="Return to draft", who="operate")
    )
    flow.step(
        Step.make("draft", "form")
        .assignee(starter())
        .schema([draft])
        .on("submit", to="split", status="review", label="Submit")
    )
    return _rest(flow)


def _rest(flow: Workflow) -> Workflow:
    flow.step(
        Step.make("split", "fork")
        .on("legal", to="legal", status="review", label="Legal")
        .on("finance", to="finance", status="review", label="Finance")
    )
    flow.step(
        Step.make("legal", "approval")
        .assignee(role("legal"))
        .on("approve", to="join", status="review", label="Approve")
        .on("return", to="draft", status="changes", label="Send back", branch="abort")
        .escalate(after="2d", to="director", status="review", label="Escalated")
        .remind("1d")
    )
    flow.step(
        Step.make("finance", "approval")
        .assignee(role("finance"))
        .on("approve", to="join", status="review", label="Approve")
        .on("return", to="draft", status="changes", label="Send back", branch="abort")
    )
    flow.step(
        Step.make("director", "approval")
        .assignee(user("director"))
        .on("approve", to="join", status="review", label="Approve")
    )
    flow.step(
        Step.make("join", "join")
        .joins(["legal", "finance", "director"], "quorum", 2)
        .on("go", to="sign", status="review", label="Ready to sign")
    )
    flow.step(
        Step.make("sign", "sign")
        .assignee(starter())
        .signs("sign")
        .statement("I agree to this contract.")
        .schema(
            [
                {
                    "type": "SignatureInput",
                    "name": "signature",
                    "label": "Signature",
                    "required": True,
                }
            ]
        )
        .on("sign", to="end", status="executed", label="Sign")
    )
    return flow
