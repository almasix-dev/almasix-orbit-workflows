"""Register inbox, case, and canvas pages on a panel."""

from __future__ import annotations

from typing import Any, ClassVar

from almasix.orbit.panels.hooks import Plugin
from almasix.orbit.panels.page import Page

from almasix_orbit_workflows.engine import WorkflowEngine
from almasix_orbit_workflows.views import render_canvas, render_case, render_inbox, render_task

_ENGINE: WorkflowEngine | None = None


def install(engine: WorkflowEngine) -> WorkflowEngine:
    """Pages render whatever engine was installed, usually by :class:`WorkflowPlugin`."""
    global _ENGINE
    _ENGINE = engine
    return engine


def current_engine() -> WorkflowEngine:
    if _ENGINE is None:
        return install(WorkflowEngine())
    return _ENGINE


class InboxPage(Page):
    slug: ClassVar[str] = "workflows"
    title: ClassVar[str] = "Inbox"
    navigation_label: ClassVar[str] = "Workflows"
    navigation_icon: ClassVar[str] = "heroicon-o-queue-list"
    navigation_group: ClassVar[str] = "Workflows"
    navigation_sort: ClassVar[int] = 10

    @classmethod
    def render(cls, **ctx: Any) -> str:
        actor = str(ctx.get("actor_id") or getattr(ctx.get("user"), "id", "") or "")
        return render_inbox(current_engine(), actor)


class CasePage(Page):
    slug: ClassVar[str] = "workflow-case"
    title: ClassVar[str] = "Case"
    navigation_label: ClassVar[str] = "Cases"
    navigation_icon: ClassVar[str] = "heroicon-o-document-text"
    navigation_group: ClassVar[str] = "Workflows"
    navigation_sort: ClassVar[int] = 11

    @classmethod
    def render(cls, **ctx: Any) -> str:
        case_id = str(ctx.get("case_id") or "")
        task_id = str(ctx.get("task_id") or "")
        engine = current_engine()
        if task_id:
            return render_task(engine, task_id, guest=bool(ctx.get("guest")))
        return render_case(engine, case_id)


class CanvasPage(Page):
    slug: ClassVar[str] = "workflow-designer"
    title: ClassVar[str] = "Designer"
    navigation_label: ClassVar[str] = "Designer"
    navigation_icon: ClassVar[str] = "heroicon-o-pencil-square"
    navigation_group: ClassVar[str] = "Workflows"
    navigation_sort: ClassVar[int] = 12

    @classmethod
    def render(cls, **ctx: Any) -> str:
        document = ctx.get("document")
        if not isinstance(document, dict):
            engine = current_engine()
            key = str(ctx.get("key") or "")
            record = engine.store.definition(key) if key else None
            document = (record or {}).get("draft") or {"key": key, "statuses": [], "steps": []}
        return render_canvas(document)


class WorkflowPlugin(Plugin):
    """Mounts the inbox, the case, and the canvas. Pass an engine to share storage with the app."""

    def __init__(self, engine: WorkflowEngine | None = None) -> None:
        super().__init__("orbit-workflows")
        self.engine = engine or WorkflowEngine()

    @classmethod
    def make(cls, engine: WorkflowEngine | None = None) -> WorkflowPlugin:
        return cls(engine)

    def register(self, panel: Any) -> None:
        install(self.engine)
        pages = getattr(panel, "pages", None)
        if callable(pages):
            pages([InboxPage, CasePage, CanvasPage])

    def boot(self, panel: Any) -> None:
        del panel
        install(self.engine)
