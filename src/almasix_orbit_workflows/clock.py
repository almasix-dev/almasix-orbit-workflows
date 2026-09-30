"""Deadlines in the workflow's timezone. Days follow the calendar in that zone."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from almasix_orbit_workflows.errors import Invalid


class Clock:
    """Injectable time source. Tests pass a fixed ``datetime``."""

    def __init__(self, now: datetime | None = None) -> None:
        self._fixed = now

    def now(self) -> datetime:
        if self._fixed is not None:
            moment = self._fixed
        else:
            moment = datetime.now(UTC)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(UTC)

    def advance(self, **delta: float) -> None:
        """Move a fixed clock. Used by tests and by nothing else."""
        self._fixed = self.now() + timedelta(**delta)


def ensure_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except ZoneInfoNotFoundError as exc:
        raise Invalid(f"Unknown timezone '{name}'.") from exc


def parse_duration(text: str) -> tuple[str, int]:
    """Return ``("days"|"seconds", amount)`` for values like ``2d``, ``4h``, ``30m``."""
    raw = str(text or "").strip().lower()
    if len(raw) < 2 or not raw[:-1].isdigit():
        raise Invalid(f"Duration '{text}' should look like 2d, 4h, or 30m.")
    amount = int(raw[:-1])
    unit = raw[-1]
    if unit == "d":
        return ("days", amount)
    if unit == "h":
        return ("seconds", amount * 3600)
    if unit == "m":
        return ("seconds", amount * 60)
    raise Invalid(f"Duration '{text}' should look like 2d, 4h, or 30m.")


def add_duration(moment: datetime, text: str, zone: str) -> datetime:
    kind, amount = parse_duration(text)
    local = moment.astimezone(ensure_zone(zone))
    if kind == "days":
        shifted = local + timedelta(days=amount)
    else:
        shifted = local + timedelta(seconds=amount)
    return shifted.astimezone(UTC)


def subtract_duration(moment: datetime, text: str, zone: str) -> datetime:
    kind, amount = parse_duration(text)
    local = moment.astimezone(ensure_zone(zone))
    if kind == "days":
        shifted = local - timedelta(days=amount)
    else:
        shifted = local - timedelta(seconds=amount)
    return shifted.astimezone(UTC)


def stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(value: str) -> datetime:
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)
