"""Errors a caller can branch on. ``code`` is stable; the message is for people."""

from __future__ import annotations


class WorkflowError(Exception):
    """Base error. ``code`` is a short machine key (``conflict``, ``refused``, ...)."""

    code = "error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class DocumentError(WorkflowError):
    """The definition cannot be published."""

    code = "document"

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors), code="document")


class Conflict(WorkflowError):
    """Someone else already moved this task, or this completion was already applied."""

    code = "conflict"


class Refused(WorkflowError):
    """An effect declined the edge. The task stays open."""

    code = "refused"


class Forbidden(WorkflowError):
    """The actor is not allowed to do this."""

    code = "forbidden"


class NotFound(WorkflowError):
    """Case, task, definition, or guest link does not exist."""

    code = "not_found"


class Invalid(WorkflowError):
    """Payload, outcome, or expression is not acceptable. The task stays open."""

    code = "invalid"
