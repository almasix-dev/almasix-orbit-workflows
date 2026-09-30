"""What a signature binds. The snapshot is stored; later edits do not rewrite it."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def snapshot(answers: dict[str, Any], files: dict[str, str] | None = None) -> dict[str, Any]:
    """JSON-stable copy of the answers plus sha256 of each named file's bytes."""
    return {
        "answers": json.loads(json.dumps(answers, sort_keys=True, default=str)),
        "files": dict(sorted((files or {}).items())),
    }


def fingerprint(body: dict[str, Any], *, intent: str, signer: str) -> str:
    packed = json.dumps({"snapshot": body, "intent": intent, "signer": signer}, sort_keys=True)
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


def file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
