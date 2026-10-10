"""Small helpers for immutable research records; no external packages required."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
ROOT = PROJECT.parents[1]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=True, allow_nan=False)
        stream.write("\n")


def settings():
    return json.loads((PROJECT / "settings.json").read_text(encoding="utf-8"))


def code_identity():
    return {p.name: sha256(p) for p in sorted(PROJECT.glob("*.py"))} | {
        "settings.json": sha256(PROJECT / "settings.json")
    }
