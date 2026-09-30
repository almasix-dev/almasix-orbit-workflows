"""Who is asked to act. Resolved when a task opens, then stored on the task."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Person:
    """A concrete actor. Guests have an email and no panel account."""

    id: str
    label: str
    guest: bool = False
    email: str | None = None

    @classmethod
    def of(cls, user_id: str, label: str | None = None) -> Person:
        return cls(id=str(user_id), label=label or str(user_id))


class Directory:
    """Panel users and roles the workflow may assign. Pass records, not a second user table."""

    def __init__(self, people: list[dict[str, Any]] | None = None) -> None:
        self._people = [dict(item) for item in people or []]

    def replace(self, people: list[dict[str, Any]]) -> None:
        self._people = [dict(item) for item in people]

    def get(self, user_id: str | None) -> Person | None:
        if not user_id:
            return None
        for row in self._people:
            if str(row.get("id")) == str(user_id):
                return Person.of(str(row["id"]), str(row.get("name") or row["id"]))
        return None

    def with_role(self, role_name: str) -> list[Person]:
        found: list[Person] = []
        for row in self._people:
            roles = [str(item) for item in row.get("roles") or []]
            if role_name in roles:
                found.append(Person.of(str(row["id"]), str(row.get("name") or row["id"])))
        return found

    def manager_of(self, user_id: str) -> Person | None:
        for row in self._people:
            if str(row.get("id")) == str(user_id) and row.get("manager_id"):
                return self.get(str(row["manager_id"]))
        return None

    def has_role(self, user_id: str, role_name: str) -> bool:
        for row in self._people:
            if str(row.get("id")) == str(user_id) and role_name in (row.get("roles") or []):
                return True
        return False


def resolve_assignees(
    specs: list[dict[str, str]],
    *,
    directory: Directory,
    starter_id: str,
    answers: dict[str, Any],
) -> list[Person]:
    """Turn document assignee rules into people. Empty means the task is unassigned."""
    people: list[Person] = []
    seen: set[str] = set()
    for spec in specs:
        for person in _one(spec, directory=directory, starter_id=starter_id, answers=answers):
            if person.id in seen:
                continue
            seen.add(person.id)
            people.append(person)
    return people


def _one(
    spec: dict[str, str], *, directory: Directory, starter_id: str, answers: dict[str, Any]
) -> list[Person]:
    kind = spec.get("kind")
    value = spec.get("value") or ""
    if kind == "user":
        found = directory.get(value)
        return [found] if found else []
    if kind == "role":
        return directory.with_role(value)
    if kind == "starter":
        found = directory.get(starter_id)
        return [found or Person.of(starter_id, starter_id)]
    if kind == "field":
        picked = answers.get(value)
        if not picked:
            return []
        found = directory.get(str(picked))
        return [found or Person.of(str(picked), str(picked))]
    if kind == "expression":
        if value.startswith("manager_of:"):
            found = directory.manager_of(
                starter_id if value.endswith("starter") else value.split(":", 1)[1]
            )
            return [found] if found else []
        if value.startswith("role_except_starter:"):
            role_name = value.split(":", 1)[1]
            return [person for person in directory.with_role(role_name) if person.id != starter_id]
        return []
    if kind == "guest":
        email = value.strip().lower()
        if not email:
            return []
        label = spec.get("label") or email
        return [Person(id=f"guest:{email}", label=label, guest=True, email=email)]
    return []
