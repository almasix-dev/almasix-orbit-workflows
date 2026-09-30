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
        f"<li><time>{e(event['at'])}</time> {e(event['label'])} {e(event['kind'])}</li>"
        for event in engine.store.events_for(case_id)
    )
    tokens = "".join(
        f'<li data-state="{e(task["state"])}">{e(task["step"])}</li>'
        for task in engine.store.tasks_for(case_id)
        if task["state"] == "open"
    )
    rows = engine.store.signatures_for(case_id)
    signatures = ""
    if rows:
        items = "".join(
            f"<li>{e(row['name'])} <code>{e(row['hash'][:12])}</code></li>" for row in rows
        )
        signatures = f'<h2>Signatures</h2><ul class="wf-signatures">{items}</ul>'
    return (
        f'<article class="wf-case" data-status="{e(case.get("status") or "")}">'
        f"<h1>{e(case['key'])}</h1>"
        f'<p class="wf-status" data-color="{e(status["color"])}">{e(status["label"])}</p>'
        f'<h2>Open work</h2><ul class="wf-tokens">{tokens}</ul>'
        f'<h2>Timeline</h2><ol class="wf-timeline">{events}</ol>'
        f"{signatures}"
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
        f'<h1>{e(step["key"])}</h1>{form}<div class="wf-outcomes">{buttons}</div></form>'
    )


_NODE_W = 220
_GAP_X = 88
_ROW_GAP = 96


def render_canvas(document: dict[str, Any]) -> str:
    """Steps as cards, with an arrow for every transition."""
    issues = problems(document)
    notice = "".join(f"<li>{e(item)}</li>" for item in issues)
    statuses = "".join(
        f'<li data-terminal="{str(bool(item.get("terminal"))).lower()}">{e(item["label"])}</li>'
        for item in document.get("statuses") or []
    )
    checks = f'<h2>Checks</h2><ul class="wf-problems">{notice}</ul>' if issues else ""
    return (
        f'<div class="wf-canvas"><aside><h2>Statuses</h2><ul>{statuses}</ul>'
        f"{checks}</aside>"
        f'<div class="wf-stage">{_graph(document)}</div></div>'
    )


def _graph(document: dict[str, Any]) -> str:
    steps, links, keys = _links(document)
    if not keys:
        return '<div class="wf-graph"></div>'
    known = {step["key"]: step for step in steps}
    backs = _backs(keys, links)
    rank = _ranks(str(document.get("start") or ""), keys, links, backs)
    placed, width, height = _arrange(keys, rank, known, links, backs)
    return (
        f'<div class="wf-graph" style="width:{width}px;height:{height}px">'
        f"{_arrows(links, placed, backs, width, height)}"
        f'<div class="wf-nodes">{_cards(placed, known)}</div></div>'
    )


def _links(
    document: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[tuple[str, str, str, str]], list[str]]:
    steps = list(document.get("steps") or [])
    known = {step["key"] for step in steps}
    links: list[tuple[str, str, str, str]] = []
    extras: list[str] = []
    seen: set[str] = set()

    def extra(dest: str) -> None:
        if dest not in known and dest not in seen:
            seen.add(dest)
            extras.append(dest)

    for step in steps:
        for edge in step.get("edges") or []:
            dest = str(edge.get("to") or "")
            if not dest:
                continue
            extra(dest)
            kind = "abort" if edge.get("branch") == "abort" else "forward"
            links.append(
                (step["key"], dest, str(edge.get("label") or edge.get("key") or dest), kind)
            )
        rule = step.get("escalate")
        if isinstance(rule, dict) and rule.get("to"):
            dest = str(rule["to"])
            extra(dest)
            links.append((step["key"], dest, str(rule.get("label") or "Escalated"), "escalate"))
    return steps, links, [step["key"] for step in steps] + extras


def _backs(keys: list[str], links: list[tuple[str, str, str, str]]) -> set[int]:
    adjacent: dict[str, list[tuple[str, int]]] = {key: [] for key in keys}
    for index, (src, dst, _, kind) in enumerate(links):
        if kind == "forward" and src in adjacent and dst in adjacent:
            adjacent[src].append((dst, index))
    color = dict.fromkeys(keys, 0)
    found: set[int] = set()

    def walk(node: str) -> None:
        color[node] = 1
        for dest, index in adjacent[node]:
            if color.get(dest) == 1:
                found.add(index)
            elif color.get(dest) == 0:
                walk(dest)
        color[node] = 2

    for key in keys:
        if color[key] == 0:
            walk(key)
    return found


def _ranks(
    start: str,
    keys: list[str],
    links: list[tuple[str, str, str, str]],
    backs: set[int],
) -> dict[str, int]:
    rank: dict[str, int | None] = dict.fromkeys(keys)
    parents: dict[str, list[str]] = {key: [] for key in keys}
    for index, (src, dst, _, kind) in enumerate(links):
        if kind == "forward" and index not in backs and dst in parents:
            parents[dst].append(src)
    rank[start if start in rank else keys[0]] = 0
    for _ in keys:
        for dest, srcs in parents.items():
            best = rank[dest]
            for src in srcs:
                if rank[src] is None:
                    continue
                option = (rank[src] or 0) + 1
                if best is None or option > best:
                    best = option
            rank[dest] = best
    for src, dst, _, kind in links:
        if kind != "escalate" or rank[src] is None or rank[dst] is not None:
            continue
        rank[dst] = rank[src]
    trailing = max(value for value in rank.values() if value is not None) + 1
    for key, value in rank.items():
        if value is None:
            rank[key] = trailing
    pending = True
    left = len(keys)
    while left and pending:
        left -= 1
        pending = _push(rank, links, backs)
    return {key: int(value or 0) for key, value in rank.items()}


def _push(
    rank: dict[str, int | None],
    links: list[tuple[str, str, str, str]],
    backs: set[int],
) -> bool:
    moved = False
    for index, (src, dst, _, kind) in enumerate(links):
        if kind != "forward" or (index in backs and src != dst):
            continue
        if int(rank[dst] or 0) <= int(rank[src] or 0):
            rank[dst] = int(rank[src] or 0) + 1
            moved = True
    return moved


def _arrange(
    keys: list[str],
    rank: dict[str, int],
    known: dict[str, dict[str, Any]],
    links: list[tuple[str, str, str, str]],
    backs: set[int],
) -> tuple[dict[str, dict[str, Any]], int, int]:
    heights = {key: _card_height(len((known.get(key) or {}).get("schema") or [])) for key in keys}
    rows: dict[int, list[str]] = {}
    for key in keys:
        rows.setdefault(rank[key], []).append(key)
    for level, group in rows.items():
        rows[level] = _beside(group, links, level, rank)
    ordered = sorted(rows)
    first = ordered[0]
    left_rails, right_rails, lifted = _margins(links, rows, rank, backs, first)
    pad_left = 128 if left_rails else 20
    pad_right = 128 if right_rails else 16
    pad_top = 16 + lifted * 18
    row_width = {
        level: len(group) * _NODE_W + max(0, len(group) - 1) * _GAP_X
        for level, group in rows.items()
    }
    inner = max(row_width.values())
    placed: dict[str, dict[str, Any]] = {}
    top = pad_top
    for level in ordered:
        group = rows[level]
        row_height = max(heights[key] for key in group)
        left = pad_left + (inner - row_width[level]) / 2
        for key in group:
            placed[key] = {
                "key": key,
                "x": int(round(left)),
                "y": int(round(top + (row_height - heights[key]) / 2)),
                "w": _NODE_W,
                "h": heights[key],
                "rank": level,
            }
            left += _NODE_W + _GAP_X
        top += row_height + _ROW_GAP
    return placed, int(pad_left + inner + pad_right), int(top - _ROW_GAP + 16)


def _beside(
    group: list[str],
    links: list[tuple[str, str, str, str]],
    level: int,
    rank: dict[str, int],
) -> list[str]:
    """Put an escalation target immediately beside the step that escalates to it."""
    guests = [
        dst
        for src, dst, _, kind in links
        if kind == "escalate" and rank.get(src) == level and rank.get(dst) == level and dst in group
    ]
    ordered = [key for key in group if key not in guests]
    for src, dst, _, kind in links:
        if kind != "escalate" or dst not in guests or rank.get(src) != level:
            continue
        guests.remove(dst)
        if src in ordered:
            ordered.insert(ordered.index(src) + 1, dst)
        else:
            ordered.append(dst)
    return ordered


def _margins(
    links: list[tuple[str, str, str, str]],
    rows: dict[int, list[str]],
    rank: dict[str, int],
    backs: set[int],
    first: int,
) -> tuple[int, int, int]:
    left = right = lifted = 0
    for index, (src, dst, _, _) in enumerate(links):
        shape = _shape(src, dst, rank[src], rank[dst], index in backs)
        if shape == "rail":
            group = rows[rank[src]]
            if group.index(src) < len(group) / 2:
                left += 1
            else:
                right += 1
        if shape in {"across", "loop", "rail"} and rank[src] == first:
            lifted += 1
    return left, right, lifted


def _card_height(fields: int) -> int:
    base = 60
    if fields:
        return base + 6 + 16 * fields
    return base


def _shape(src: str, dst: str, src_rank: int, dst_rank: int, back: bool) -> str:
    if src == dst:
        return "loop"
    if dst_rank > src_rank and not back:
        return "down"
    if dst_rank == src_rank:
        return "across"
    return "rail"


def _cards(placed: dict[str, dict[str, Any]], known: dict[str, dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in placed.values():
        step = known.get(item["key"])
        if item["key"] == "end":
            kind, title = "end", "End"
        elif step is None:
            kind, title = "missing", item["key"]
        else:
            kind, title = step["kind"], item["key"]
        fields = ""
        if step and step.get("schema"):
            fields = "".join(
                f"<li>{e(node.get('type'))} {e(node.get('name') or '')}</li>"
                for node in step["schema"]
            )
            fields = f'<ul class="wf-schema">{fields}</ul>'
        parts.append(
            f'<section class="wf-node" data-kind="{e(kind)}" data-step="{e(item["key"])}" '
            f'style="left:{item["x"]}px;top:{item["y"]}px;width:{item["w"]}px;height:{item["h"]}px">'
            f"<h2>{e(title)}</h2>{fields}</section>"
        )
    return "".join(parts)


def _arrows(
    links: list[tuple[str, str, str, str]],
    placed: dict[str, dict[str, Any]],
    backs: set[int],
    width: int,
    height: int,
) -> str:
    down_out: dict[str, list[int]] = {}
    down_in: dict[str, list[int]] = {}
    shapes: list[str] = []
    for index, (src, dst, _, _) in enumerate(links):
        shape = _shape(src, dst, placed[src]["rank"], placed[dst]["rank"], index in backs)
        shapes.append(shape)
        if shape == "down":
            down_out.setdefault(src, []).append(index)
            down_in.setdefault(dst, []).append(index)
    paths: list[str] = []
    labels: list[str] = []
    lifts: dict[int, int] = {}
    left_lane = 0
    right_lane = 0
    for index, (src, dst, label, kind) in enumerate(links):
        source, target = placed[src], placed[dst]
        shape = shapes[index]
        if shape == "down":
            path, x, y = _down(source, target, down_out[src], down_in[dst], index)
        elif shape == "loop":
            path, x, y = _loop(source, 18 + lifts.get(source["rank"], 0) * 16)
            lifts[source["rank"]] = lifts.get(source["rank"], 0) + 1
        elif shape == "across":
            path, x, y = _across(source, target, 28 + lifts.get(source["rank"], 0) * 16)
            lifts[source["rank"]] = lifts.get(source["rank"], 0) + 1
        else:
            side = "left" if source["x"] + source["w"] / 2 < width / 2 else "right"
            if side == "left":
                lane = 28 + left_lane * 18
                left_lane += 1
            else:
                lane = width - 28 - right_lane * 18
                right_lane += 1
            path, x, y = _rail(source, target, lane, side)
        paths.append(
            f'<path class="wf-edge wf-edge-{kind}" d="{path}" marker-end="url(#wf-arrow-{kind})"/>'
        )
        labels.append(_edge_label(x, y, label))
    box = "0 0 10 10"
    marker = (
        "<defs>"
        f'<marker id="wf-arrow-forward" viewBox="{box}" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#f1511b"/></marker>'
        f'<marker id="wf-arrow-abort" viewBox="{box}" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#c2410c"/></marker>'
        f'<marker id="wf-arrow-escalate" viewBox="{box}" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#b45309"/></marker>'
        "</defs>"
    )
    return (
        f'<svg class="wf-arrows" viewBox="0 0 {width} {height}">'
        f"{marker}{''.join(paths)}{''.join(labels)}</svg>"
    )


def _down(
    source: dict[str, Any],
    target: dict[str, Any],
    outs: list[int],
    ins: list[int],
    index: int,
) -> tuple[str, float, float]:
    x1 = _spread(outs.index(index), len(outs), source["x"] + source["w"] / 2, source["w"] - 36)
    x2 = _spread(ins.index(index), len(ins), target["x"] + target["w"] / 2, target["w"] - 36)
    y1 = float(source["y"] + source["h"])
    y2 = float(target["y"])
    mid = (y1 + y2) / 2
    path = (
        f"M {_px(x1)} {_px(y1)} C {_px(x1)} {_px(mid)}, {_px(x2)} {_px(mid)}, {_px(x2)} {_px(y2)}"
    )
    return path, (x1 + x2) / 2, mid - 10


def _across(source: dict[str, Any], target: dict[str, Any], lift: int) -> tuple[str, float, float]:
    toward_right = target["x"] >= source["x"]
    x1 = source["x"] + source["w"] if toward_right else source["x"]
    x2 = target["x"] if toward_right else target["x"] + target["w"]
    bow = min(source["y"], target["y"]) - lift
    path = (
        f"M {_px(x1)} {_px(source['y'] + 20)} L {_px(x1)} {_px(bow)} "
        f"L {_px(x2)} {_px(bow)} L {_px(x2)} {_px(target['y'] + 20)}"
    )
    return path, (x1 + x2) / 2, bow


def _loop(node: dict[str, Any], lift: int) -> tuple[str, float, float]:
    x1 = node["x"] + 28
    x2 = node["x"] + node["w"] - 28
    y = node["y"] - lift
    path = (
        f"M {_px(x1)} {node['y']} C {_px(x1)} {_px(y)}, {_px(x2)} {_px(y)}, {_px(x2)} {node['y']}"
    )
    return path, (x1 + x2) / 2, y


def _rail(
    source: dict[str, Any], target: dict[str, Any], lane: int, side: str
) -> tuple[str, float, float]:
    y0 = source["y"] + source["h"] / 2
    y1 = target["y"] + target["h"] / 2
    if side == "left":
        x0, x1 = source["x"], target["x"]
        label_x = (lane + source["x"]) / 2
    else:
        x0, x1 = source["x"] + source["w"], target["x"] + target["w"]
        label_x = (lane + x0) / 2
    path = f"M {_px(x0)} {_px(y0)} L {lane} {_px(y0)} L {lane} {_px(y1)} L {_px(x1)} {_px(y1)}"
    return path, label_x, y0 - 14


def _edge_label(x: float, y: float, text: str) -> str:
    width = max(28, int(len(text) * 6.6) + 14)
    left = x - width / 2
    return (
        '<g class="wf-edge-label">'
        f'<rect x="{_px(left)}" y="{_px(y - 8)}" width="{width}" height="16" rx="8"/>'
        f'<text x="{_px(x)}" y="{_px(y)}">{e(text)}</text></g>'
    )


def _spread(index: int, count: int, center: float, room: float) -> float:
    if count <= 1:
        return center
    span = min(room, (count - 1) * 36)
    return center - span / 2 + span * index / (count - 1)


def _px(value: float) -> int:
    return int(round(value))


def _label(engine: WorkflowEngine, case: dict[str, Any]) -> dict[str, str]:
    if not case or not case.get("key"):
        return {"label": "", "color": "gray"}
    try:
        document = engine.document_for(case["key"], case.get("version"))
    except Exception:
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
        if task["state"] == "open"
        and (not task["assignees"] or any(item["id"] == actor_id for item in task["assignees"]))
    ]
