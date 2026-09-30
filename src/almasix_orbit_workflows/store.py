"""Process-local persistence. Swap this for a database by matching the same methods."""

from __future__ import annotations

import threading
from typing import Any
from uuid import uuid4


class MemoryStore:
    """One process, one lock. Completions serialize so two approvers cannot both win."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.definitions: dict[str, dict[str, Any]] = {}
        self.cases: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.signatures: list[dict[str, Any]] = []
        self.applied: set[str] = set()

    def lock(self) -> threading.RLock:
        return self._lock

    def new_id(self) -> str:
        return uuid4().hex

    def save_definition(self, record: dict[str, Any]) -> None:
        self.definitions[record["key"]] = record

    def definition(self, key: str) -> dict[str, Any] | None:
        found = self.definitions.get(key)
        return None if found is None else found

    def save_case(self, case: dict[str, Any]) -> None:
        self.cases[case["id"]] = case

    def case(self, case_id: str) -> dict[str, Any] | None:
        return self.cases.get(case_id)

    def save_task(self, task: dict[str, Any]) -> None:
        self.tasks[task["id"]] = task

    def task(self, task_id: str) -> dict[str, Any] | None:
        return self.tasks.get(task_id)

    def tasks_for(self, case_id: str) -> list[dict[str, Any]]:
        return [task for task in self.tasks.values() if task["case_id"] == case_id]

    def add_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def events_for(self, case_id: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event["case_id"] == case_id]

    def add_signature(self, row: dict[str, Any]) -> None:
        self.signatures.append(row)

    def signatures_for(self, case_id: str) -> list[dict[str, Any]]:
        return [row for row in self.signatures if row["case_id"] == case_id]
