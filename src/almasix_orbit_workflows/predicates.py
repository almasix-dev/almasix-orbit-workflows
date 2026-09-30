"""Tiny predicate language stored in the document. It is not Python."""

from __future__ import annotations

from typing import Any

from almasix_orbit_workflows.errors import Invalid

_OPS = ("!=", ">=", "<=", "==", ">", "<")


def matches(expression: str | None, answers: dict[str, Any], effects: dict[str, Any], context: dict[str, Any]) -> bool:
    """True when ``expression`` holds. A blank expression is the default edge."""
    if expression is None or str(expression).strip() == "":
        return True
    text = str(expression).strip()
    if text.startswith("effect:"):
        name = text.split(":", 1)[1].strip()
        fn = effects.get(name)
        if fn is None:
            raise Invalid(f"Unknown effect '{name}'.")
        return bool(fn(answers, context))
    if not text.startswith("answers."):
        raise Invalid(f"Cannot read expression '{text}'.")
    body = text[len("answers.") :]
    op = next((item for item in _OPS if item in body), None)
    if op is None:
        raise Invalid(f"Cannot read expression '{text}'.")
    field, raw = body.split(op, 1)
    field = field.strip()
    left = answers.get(field)
    right = _literal(raw.strip())
    return _compare(left, op, right)


def _literal(raw: str) -> Any:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
        return raw[1:-1]
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"none", "null"}:
        return None
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError as exc:
        raise Invalid(f"Cannot read value '{raw}'.") from exc


def _compare(left: Any, op: str, right: Any) -> bool:
    if op == "==":
        return left == right or str(left) == str(right)
    if op == "!=":
        return not (left == right or str(left) == str(right))
    try:
        pair = (float(left), float(right))
    except (TypeError, ValueError) as exc:
        raise Invalid(f"Cannot compare {left!r} {op} {right!r}.") from exc
    left_n, right_n = pair
    if op == ">":
        return left_n > right_n
    if op == "<":
        return left_n < right_n
    if op == ">=":
        return left_n >= right_n
    return left_n <= right_n
