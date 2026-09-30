"""Run a published document. One lock, one completion, one effect."""

from __future__ import annotations

import secrets
from typing import Any

from almasix_orbit_workflows.actors import Directory, Person, resolve_assignees
from almasix_orbit_workflows.clock import Clock, add_duration, parse_stamp, stamp, subtract_duration
from almasix_orbit_workflows.document import require_valid
from almasix_orbit_workflows.errors import Conflict, Forbidden, Invalid, NotFound, Refused
from almasix_orbit_workflows.predicates import matches
from almasix_orbit_workflows.schema_tree import field_errors
from almasix_orbit_workflows.signing import fingerprint, snapshot
from almasix_orbit_workflows.store import MemoryStore


class Notifier:
    """Records every notification. The plugin can also fan these out to email."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def send(self, user_id: str, title: str, body: str, *, email: str | None = None) -> None:
        self.sent.append({"user_id": user_id, "title": title, "body": body, "email": email or ""})


class WorkflowEngine:
    """The runtime. Definitions, cases, and tasks live in ``store``."""

    def __init__(
        self,
        store: MemoryStore | None = None,
        directory: Directory | None = None,
        clock: Clock | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self.store = store or MemoryStore()
        self.directory = directory or Directory()
        self.clock = clock or Clock()
        self.notifier = notifier or Notifier()
        self.effects: dict[str, Any] = {}

    # —— definitions ———————————————————————————————————————————————————————

    def save(self, document: dict[str, Any]) -> dict[str, Any]:
        """Keep a draft. It cannot be started until :meth:`publish`."""
        record = self.store.definition(document["key"]) or _blank_record(document["key"])
        record["lifecycle"] = "draft" if record["published_version"] is None else record["lifecycle"]
        record["draft"] = document
        record["name"] = document.get("name") or document["key"]
        self.store.save_definition(record)
        return record

    def publish(self, key: str) -> dict[str, Any]:
        record = self._record(key)
        document = require_valid(dict(record["draft"]))
        version = int(record["version"]) + 1
        record["version"] = version
        record["versions"][str(version)] = document
        record["published_version"] = version
        record["lifecycle"] = "published"
        self.store.save_definition(record)
        return record

    def retire(self, key: str) -> dict[str, Any]:
        record = self._record(key)
        if record["published_version"] is None:
            raise Invalid(f"'{key}' has never been published.")
        record["lifecycle"] = "retired"
        self.store.save_definition(record)
        return record

    def document_for(self, key: str, version: int | None = None) -> dict[str, Any]:
        record = self._record(key)
        chosen = record["published_version"] if version is None else version
        if chosen is None or str(chosen) not in record["versions"]:
            raise NotFound(f"'{key}' has no published version.")
        return record["versions"][str(chosen)]

    # —— cases ——————————————————————————————————————————————————————————————

    def start(
        self,
        key: str,
        actor: Person,
        *,
        tenant: str | None = None,
        subject_type: str | None = None,
        subject_id: str | None = None,
        answers: dict[str, Any] | None = None,
        allow_retired: bool = False,
    ) -> dict[str, Any]:
        record = self._record(key)
        if record["lifecycle"] == "retired" and not allow_retired:
            raise Invalid(f"'{key}' is retired and cannot be started.")
        if record["published_version"] is None:
            raise Invalid(f"'{key}' is not published.")
        self._allow(actor, "start")
        document = self.document_for(key)
        with self.store.lock():
            case = {
                "id": self.store.new_id(),
                "key": key,
                "version": record["published_version"],
                "tenant": tenant,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "status": "",
                "starter_id": actor.id,
                "answers": dict(answers or {}),
                "visits": {},
                "arrivals": {},
                "closed": False,
                "created_at": stamp(self.clock.now()),
            }
            self.store.save_case(case)
            self._log(case, "started", actor, {})
            self._enter(case, document, document["start"], fork_id=None)
            self.store.save_case(case)
            return case

    def save_draft(self, task_id: str, actor: Person, payload: dict[str, Any]) -> dict[str, Any]:
        with self.store.lock():
            task, case = self._open_task(task_id, actor)
            task["draft"] = {**(task.get("draft") or {}), **payload}
            self.store.save_task(task)
            self._log(case, "draft", actor, {"task_id": task_id})
            return task

    def claim(self, task_id: str, actor: Person) -> dict[str, Any]:
        with self.store.lock():
            task, case = self._open_task(task_id, actor)
            holder = task.get("claim")
            if holder and holder != actor.id:
                raise Conflict(f"{holder} already claimed this task.")
            task["claim"] = actor.id
            self.store.save_task(task)
            self._log(case, "claimed", actor, {"task_id": task_id})
            return task

    def release(self, task_id: str, actor: Person) -> dict[str, Any]:
        with self.store.lock():
            task, case = self._open_task(task_id, actor)
            if task.get("claim") not in {None, actor.id}:
                raise Forbidden("Only the person who claimed this task can release it.")
            task["claim"] = None
            self.store.save_task(task)
            self._log(case, "released", actor, {"task_id": task_id})
            return task

    def complete(
        self,
        task_id: str,
        actor: Person,
        outcome: str,
        payload: dict[str, Any] | None = None,
        *,
        signature: dict[str, Any] | None = None,
        files: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload = dict(payload or {})
        with self.store.lock():
            task, case = self._open_task(task_id, actor)
            if task.get("claim") not in {None, actor.id} and task["policy"] == "any":
                raise Conflict("This task is claimed by someone else.")
            effect_key = f"{task_id}:{outcome}:{actor.id}"
            if effect_key in self.store.applied:
                raise Conflict("This completion was already applied.")
            document = self.document_for(case["key"], case["version"])
            step = _step(document, task["step"])
            edge = _edge(step, outcome)
            errors = field_errors(step.get("schema") or [], payload, case["answers"])
            if errors:
                raise Invalid(" ".join(errors.values()))
            if _outcome_needs_signature(step, outcome):
                self._sign(case, task, actor, signature, payload, files, step.get("intent") or "")
            case["answers"] = {**case["answers"], **payload}
            self._run_effect(edge.get("effect"), case, actor, effect_key)
            self._disagreeing(case, task, outcome)
            self.store.applied.add(effect_key)
            self._set_status(case, edge["status"])
            task["done_by"] = list(task.get("done_by") or []) + [actor.id]
            self._log(case, "completed", actor, {"task_id": task_id, "outcome": outcome, "label": edge["label"]})
            if not _group_satisfied(self.store, task, edge):
                task["state"] = "done"
                self.store.save_task(task)
                self.store.save_case(case)
                return case
            task["state"] = "done"
            self.store.save_task(task)
            self._close_group(task)
            self._after_edge(case, document, task, edge, actor)
            self.store.save_case(case)
            return case

    def reassign(self, task_id: str, actor: Person, user_id: str) -> dict[str, Any]:
        self._allow(actor, "reassign")
        with self.store.lock():
            task = self._task(task_id)
            if task["state"] != "open":
                raise Conflict("Only an open task can be reassigned.")
            case = self._case(task["case_id"])
            person = self.directory.get(user_id)
            if person is None:
                raise Invalid(f"No user '{user_id}'.")
            task["assignees"] = [{"id": person.id, "label": person.label, "guest": False, "email": None}]
            task["unassigned"] = False
            task["claim"] = None
            task["guest_token"] = None
            self.store.save_task(task)
            self._log(case, "reassigned", actor, {"task_id": task_id, "user_id": user_id})
            self._notify_people([person], case, "A task was reassigned to you.")
            return task

    def comment(self, case_id: str, actor: Person, text: str) -> dict[str, Any]:
        case = self._case(case_id)
        self._allow(actor, "view", case)
        self._log(case, "comment", actor, {"text": text})
        return case

    def take_case_action(self, case_id: str, actor: Person, action_key: str) -> dict[str, Any]:
        with self.store.lock():
            case = self._case(case_id)
            if case["closed"]:
                raise Conflict("This case is already finished.")
            document = self.document_for(case["key"], case["version"])
            action = next((item for item in document.get("case_actions") or [] if item["key"] == action_key), None)
            if action is None:
                raise Invalid(f"Unknown case action '{action_key}'.")
            self._allow_case_action(actor, action, case)
            self._set_status(case, action["status"])
            self._cancel_open(case["id"])
            status = _status(document, action["status"])
            if status.get("terminal"):
                case["closed"] = True
            self._log(case, "case_action", actor, {"action": action_key, "label": action["label"]})
            self.store.save_case(case)
            return case

    def promote_due(self) -> list[str]:
        """Fire reminders and escalations whose time has come. Safe to run from two schedulers."""
        moved: list[str] = []
        now = self.clock.now()
        with self.store.lock():
            for task in list(self.store.tasks.values()):
                if task["state"] != "open" or not task.get("due_at"):
                    continue
                case = self._case(task["case_id"])
                document = self.document_for(case["key"], case["version"])
                step = _step(document, task["step"])
                due = parse_stamp(task["due_at"])
                self._remind(case, task, step, document, now, due)
                if now < due or task.get("escalated"):
                    continue
                rule = step["escalate"]
                task["escalated"] = True
                task["state"] = "done"
                self.store.save_task(task)
                self._set_status(case, rule["status"])
                self._log(case, "escalated", Person.of("system", "Scheduler"), {"task_id": task["id"], "label": rule["label"]})
                self._go(case, document, rule["to"], fork_id=task.get("fork_id"), branch="finish")
                self.store.save_case(case)
                moved.append(task["id"])
        return moved

    def guest_task(self, token: str) -> dict[str, Any]:
        for task in self.store.tasks.values():
            if task.get("guest_token") == token and task["state"] == "open":
                case = self._case(task["case_id"])
                return {"task": task, "case": case, "step": _step(self.document_for(case["key"], case["version"]), task["step"])}
        raise NotFound("This link is no longer valid.")

    def simulate(self, document: dict[str, Any], script: list[dict[str, Any]], *, actor: Person) -> dict[str, Any]:
        """Walk sample actions against a private store. Nothing is written to this engine."""
        trial = WorkflowEngine(directory=self.directory, clock=self.clock)
        trial.effects = dict(self.effects)
        trial.save(document)
        trial.publish(document["key"])
        case = None
        for action in script:
            kind = action["action"]
            if kind == "start":
                case = trial.start(document["key"], actor, answers=action.get("answers"))
            elif case is not None and kind == "complete":
                task = _sole_open(trial.store, case["id"], action.get("step"))
                case = trial.complete(task["id"], actor, action["outcome"], action.get("payload"), signature=action.get("signature"))
            elif case is not None and kind == "advance":
                trial.clock.advance(**(action.get("delta") or {"days": 3}))
                trial.promote_due()
                case = trial.store.case(case["id"]) or case
        if case is None:
            raise Invalid("A simulation needs a start action.")
        return case

    # —— internals ———————————————————————————————————————————————————————————

    def _record(self, key: str) -> dict[str, Any]:
        found = self.store.definition(key)
        if found is None:
            raise NotFound(f"No workflow '{key}'.")
        return found

    def _case(self, case_id: str) -> dict[str, Any]:
        found = self.store.case(case_id)
        if found is None:
            raise NotFound("No such case.")
        return found

    def _task(self, task_id: str) -> dict[str, Any]:
        found = self.store.task(task_id)
        if found is None:
            raise NotFound("No such task.")
        return found

    def _open_task(self, task_id: str, actor: Person) -> tuple[dict[str, Any], dict[str, Any]]:
        task = self._task(task_id)
        if task["state"] != "open":
            raise Conflict("This task is no longer open.")
        ids = {item["id"] for item in task["assignees"]}
        if ids and actor.id not in ids:
            raise Forbidden("This task is not assigned to you.")
        if task.get("unassigned"):
            raise Forbidden("This task has no assignee yet.")
        return task, self._case(task["case_id"])

    def _allow(self, actor: Person, ability: str, case: dict[str, Any] | None = None) -> None:
        del case
        if actor.id == "system":
            return
        if ability in {"start", "view", "reassign"} and actor.id:
            return
        if not actor.id:
            raise Forbidden("Sign in to do this.")

    def _allow_case_action(self, actor: Person, action: dict[str, str], case: dict[str, Any]) -> None:
        who = action.get("who") or ""
        if who == "starter" and actor.id == case["starter_id"]:
            return
        if who == "operate" and self.directory.has_role(actor.id, "operate"):
            return
        if who.startswith("role:") and self.directory.has_role(actor.id, who.split(":", 1)[1]):
            return
        raise Forbidden("You cannot take this action on the case.")

    def _enter(self, case: dict[str, Any], document: dict[str, Any], step_key: str, fork_id: str | None) -> None:
        visits = case["visits"]
        visits[step_key] = int(visits.get(step_key) or 0) + 1
        if visits[step_key] > int(document.get("revisit_limit") or 10):
            stuck = document.get("stuck_status")
            self._log(case, "stuck", Person.of("system", "Scheduler"), {"step": step_key})
            if stuck:
                self._set_status(case, stuck)
            self._cancel_open(case["id"])
            self.store.save_case(case)
            return
        step = _step(document, step_key)
        if step["kind"] == "route":
            self._route(case, document, step)
            return
        if step["kind"] == "notify":
            self._notify_step(case, document, step)
            self._after_edge(case, document, {"fork_id": fork_id, "step": step_key}, step["edges"][0], Person.of("system", "Scheduler"))
            return
        if step["kind"] == "fork":
            group = self.store.new_id()
            for edge in step["edges"]:
                self._set_status(case, edge["status"])
                self._enter(case, document, edge["to"], fork_id=group)
            return
        if step["kind"] == "join":
            return
        self._open_human(case, document, step, fork_id)

    def _open_human(self, case: dict[str, Any], document: dict[str, Any], step: dict[str, Any], fork_id: str | None) -> None:
        people = resolve_assignees(step.get("assignees") or [], directory=self.directory, starter_id=case["starter_id"], answers=case["answers"])
        due = None
        if step.get("escalate"):
            due = stamp(add_duration(self.clock.now(), step["escalate"]["after"], document.get("timezone") or "UTC"))
        policy = step.get("policy") or "any"
        groups = _split_people(people, policy, int(step.get("quorum") or 1))
        if not groups:
            groups = [[]]
        shared = self.store.new_id()
        for bucket in groups:
            token = secrets.token_urlsafe(24) if any(person.guest for person in bucket) else None
            task = {
                "id": self.store.new_id(),
                "case_id": case["id"],
                "step": step["key"],
                "state": "open",
                "policy": policy,
                "quorum": int(step.get("quorum") or 1),
                "group": shared,
                "fork_id": fork_id,
                "assignees": [_person_row(person) for person in bucket],
                "unassigned": not bucket,
                "claim": None,
                "done_by": [],
                "draft": {},
                "due_at": due,
                "reminded": [],
                "escalated": False,
                "guest_token": token,
                "outcomes": {edge["key"]: edge["label"] for edge in step.get("edges") or []},
            }
            self.store.save_task(task)
            self._notify_people(bucket, case, f"{step['key']} needs you.")
            for person in bucket:
                if person.guest and token:
                    self.notifier.send(person.id, "Your step is ready", token, email=person.email)

    def _after_edge(self, case: dict[str, Any], document: dict[str, Any], task: dict[str, Any], edge: dict[str, Any], actor: Person) -> None:
        if edge.get("branch") == "abort" and task.get("fork_id"):
            self._cancel_fork(case["id"], task["fork_id"], keep=task.get("id"))
        target = edge.get("to")
        if edge.get("start_workflow"):
            self.start(edge["start_workflow"], actor, tenant=case.get("tenant"), subject_type=case.get("subject_type"), subject_id=case.get("subject_id"), answers=dict(case["answers"]), allow_retired=False)
        self._go(case, document, target, fork_id=task.get("fork_id"), branch=edge.get("branch") or "finish", arrived_from=task.get("step"))
        self._tell_watchers(case, document, edge.get("label") or "Updated")

    def _close_group(self, task: dict[str, Any]) -> None:
        for sibling in self.store.tasks_for(task["case_id"]):
            if sibling.get("group") == task.get("group") and sibling["id"] != task["id"] and sibling["state"] == "open":
                sibling["state"] = "cancelled"
                sibling["guest_token"] = None
                self.store.save_task(sibling)

    def _go(self, case: dict[str, Any], document: dict[str, Any], target: str, *, fork_id: str | None, branch: str, arrived_from: str | None = None) -> None:
        if target == "end":
            if not _any_open(self.store, case["id"]):
                case["closed"] = True
            self.store.save_case(case)
            return
        step = _step(document, target)
        if step["kind"] == "join":
            self._arrive(case, document, step, arrived_from or "", fork_id)
            return
        self._enter(case, document, target, fork_id=None if branch == "abort" else fork_id)

    def _arrive(self, case: dict[str, Any], document: dict[str, Any], step: dict[str, Any], arrived_from: str, fork_id: str | None) -> None:
        arrivals: list[str] = case["arrivals"].setdefault(step["key"], [])
        if arrived_from and arrived_from not in arrivals:
            arrivals.append(arrived_from)
        needed = list(step.get("join_from") or [])
        policy = step.get("join_policy") or "all"
        met = False
        if policy == "all":
            met = set(needed).issubset(arrivals)
        elif policy == "any":
            met = bool(arrivals)
        else:
            met = len(arrivals) >= int(step.get("quorum") or 1)
        if not met:
            self.store.save_case(case)
            return
        if fork_id:
            self._cancel_fork(case["id"], fork_id, keep=None)
        edge = step["edges"][0]
        self._set_status(case, edge["status"])
        self._go(case, document, edge["to"], fork_id=None, branch="finish", arrived_from=step["key"])

    def _route(self, case: dict[str, Any], document: dict[str, Any], step: dict[str, Any]) -> None:
        chosen = None
        for edge in step["edges"]:
            if matches(edge.get("when"), case["answers"], self.effects, {"case": case}):
                chosen = edge
                if edge.get("when"):
                    break
        if chosen is None:
            raise Invalid(f"Route '{step['key']}' had no matching edge.")
        self._set_status(case, chosen["status"])
        self._log(case, "routed", Person.of("system", "Scheduler"), {"step": step["key"], "outcome": chosen["key"]})
        self._go(case, document, chosen["to"], fork_id=None, branch=chosen.get("branch") or "finish", arrived_from=step["key"])

    def _notify_step(self, case: dict[str, Any], document: dict[str, Any], step: dict[str, Any]) -> None:
        del document
        people = resolve_assignees(step.get("assignees") or [], directory=self.directory, starter_id=case["starter_id"], answers=case["answers"])
        self._notify_people(people, case, step["edges"][0]["label"])
        self._log(case, "notified", Person.of("system", "Scheduler"), {"step": step["key"]})

    def _run_effect(self, name: str | None, case: dict[str, Any], actor: Person, effect_key: str) -> None:
        if not name:
            return
        fn = self.effects.get(name)
        if fn is None:
            raise Invalid(f"Unknown effect '{name}'.")
        try:
            fn(case["answers"], {"case": case, "actor": actor, "idempotency_key": effect_key})
        except Refused:
            raise
        except Exception as exc:  # noqa: BLE001 — app effects raise their own errors
            raise Refused(str(exc)) from exc

    def _sign(self, case: dict[str, Any], task: dict[str, Any], actor: Person, signature: dict[str, Any] | None, payload: dict[str, Any], files: dict[str, str] | None, intent: str) -> None:
        image = (signature or {}).get("image") or payload.get("signature")
        signed_name = (signature or {}).get("name") or payload.get("signer_name")
        if not image or not signed_name:
            raise Invalid("A signature and the signer's name are required.")
        body = snapshot({**case["answers"], **payload}, files)
        digest = fingerprint(body, intent=intent, signer=actor.id)
        self.store.add_signature(
            {
                "id": self.store.new_id(),
                "case_id": case["id"],
                "task_id": task["id"],
                "signer": actor.id,
                "name": signed_name,
                "image": image,
                "intent": intent,
                "hash": digest,
                "snapshot": body,
                "at": stamp(self.clock.now()),
            }
        )

    def _remind(self, case: dict[str, Any], task: dict[str, Any], step: dict[str, Any], document: dict[str, Any], now: Any, due: Any) -> None:
        zone = document.get("timezone") or "UTC"
        for duration in step.get("reminders") or []:
            if duration in (task.get("reminded") or []):
                continue
            when = subtract_duration(due, duration, zone)
            if now < when:
                continue
            task["reminded"] = list(task.get("reminded") or []) + [duration]
            self.store.save_task(task)
            people = [Person.of(item["id"], item.get("label") or item["id"]) for item in task["assignees"]]
            self._notify_people(people, case, f"Reminder: {step['key']} is due {task['due_at']}.")

    def _set_status(self, case: dict[str, Any], key: str) -> None:
        document = self.document_for(case["key"], case["version"])
        if key not in {item["key"] for item in document["statuses"]}:
            raise Invalid(f"Status '{key}' is not on this document.")
        case["status"] = key
        self.store.save_case(case)

    def _log(self, case: dict[str, Any], kind: str, actor: Person, payload: dict[str, Any]) -> None:
        self.store.add_event(
            {
                "id": self.store.new_id(),
                "case_id": case["id"],
                "kind": kind,
                "actor": actor.id,
                "label": actor.label,
                "at": stamp(self.clock.now()),
                "payload": payload,
            }
        )

    def _disagreeing(self, case: dict[str, Any], task: dict[str, Any], outcome: str) -> None:
        if task["policy"] not in {"all", "quorum"}:
            return
        for event in self.store.events_for(case["id"]):
            if event["kind"] != "completed":
                continue
            payload = event.get("payload") or {}
            sibling = self.store.task(str(payload.get("task_id") or ""))
            if sibling and sibling.get("group") == task.get("group") and payload.get("outcome") not in {None, outcome}:
                raise Conflict("This outcome does not match the rest of the group.")

    def _notify_people(self, people: list[Person], case: dict[str, Any], body: str) -> None:
        for person in people:
            if person.guest:
                continue
            self.notifier.send(person.id, case["key"], body)

    def _tell_watchers(self, case: dict[str, Any], document: dict[str, Any], label: str) -> None:
        people = resolve_assignees(document.get("watchers") or [], directory=self.directory, starter_id=case["starter_id"], answers=case["answers"])
        starter_person = self.directory.get(case["starter_id"]) or Person.of(case["starter_id"])
        if all(person.id != starter_person.id for person in people):
            people.append(starter_person)
        self._notify_people(people, case, label)

    def _cancel_open(self, case_id: str) -> None:
        for task in self.store.tasks_for(case_id):
            if task["state"] == "open":
                task["state"] = "cancelled"
                task["guest_token"] = None
                self.store.save_task(task)

    def _cancel_fork(self, case_id: str, fork_id: str, keep: str | None) -> None:
        for task in self.store.tasks_for(case_id):
            if task.get("fork_id") == fork_id and task["id"] != keep and task["state"] == "open":
                task["state"] = "cancelled"
                task["guest_token"] = None
                self.store.save_task(task)


def _blank_record(key: str) -> dict[str, Any]:
    return {"key": key, "name": key, "lifecycle": "draft", "version": 0, "published_version": None, "versions": {}, "draft": {}}


def _step(document: dict[str, Any], key: str) -> dict[str, Any]:
    for step in document["steps"]:
        if step["key"] == key:
            return step
    raise Invalid(f"Step '{key}' is not on this version.")


def _edge(step: dict[str, Any], outcome: str) -> dict[str, Any]:
    for edge in step.get("edges") or []:
        if edge["key"] == outcome:
            return edge
    raise Invalid(f"'{outcome}' is not an outcome of '{step['key']}'.")


def _status(document: dict[str, Any], key: str) -> dict[str, Any]:
    for item in document["statuses"]:
        if item["key"] == key:
            return item
    return {"key": key, "label": key, "color": "gray", "terminal": False}


def _outcome_needs_signature(step: dict[str, Any], outcome: str) -> bool:
    if step.get("kind") != "sign":
        return False
    named = step.get("sign_outcomes") or []
    return outcome in named if named else True


def _person_row(person: Person) -> dict[str, Any]:
    return {"id": person.id, "label": person.label, "guest": person.guest, "email": person.email}


def _split_people(people: list[Person], policy: str, quorum: int) -> list[list[Person]]:
    del quorum
    if policy == "any":
        return [people] if people else []
    return [[person] for person in people]


def _group_satisfied(store: MemoryStore, task: dict[str, Any], edge: dict[str, Any]) -> bool:
    """True when this outcome is allowed to advance the group (any / all / quorum)."""
    siblings = [item for item in store.tasks_for(task["case_id"]) if item.get("group") == task.get("group")]
    done = [item for item in siblings if item["id"] == task["id"] or item["state"] == "done"]
    if task["policy"] == "any":
        return True
    if task["policy"] == "all":
        return len(done) >= len(siblings)
    needed = int(task.get("quorum") or 1)
    same = [item for item in done if item["id"] == task["id"] or _same_outcome(store, item, edge["key"])]
    return len(same) >= needed


def _same_outcome(store: MemoryStore, task: dict[str, Any], outcome: str) -> bool:
    for event in reversed(store.events_for(task["case_id"])):
        payload = event.get("payload") or {}
        if payload.get("task_id") == task["id"] and event["kind"] == "completed":
            return payload.get("outcome") == outcome
    return False


def _any_open(store: MemoryStore, case_id: str) -> bool:
    return any(task["state"] == "open" for task in store.tasks_for(case_id))


def _sole_open(store: MemoryStore, case_id: str, step: str | None) -> dict[str, Any]:
    open_tasks = [task for task in store.tasks_for(case_id) if task["state"] == "open" and (step is None or task["step"] == step)]
    if not open_tasks:
        raise NotFound("No open task.")
    return open_tasks[0]
