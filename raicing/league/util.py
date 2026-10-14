"""Small helpers shared by the league services, the API and the CLI."""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

import numpy as np


def to_jsonable(obj: Any) -> Any:
    """Recursively convert numpy types, tuples and non-finite floats into strict-JSON values.

    PostgreSQL's JSON type rejects ``NaN``/``Infinity``: they become ``None``.
    """
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return to_jsonable(obj.tolist())
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if isinstance(obj, (np.integer, int)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return f if math.isfinite(f) else None
    return obj


def dumps(obj: Any) -> str:
    return json.dumps(to_jsonable(obj), ensure_ascii=False, separators=(",", ":"))


def iso(dt: datetime | None) -> str | None:
    """ISO 8601 UTC with a ``Z`` suffix (naive datetimes are assumed UTC)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def slugify(text: str, max_len: int = 40) -> str:
    """ASCII slug: ``"Écurie Aurora"`` -> ``"ecurie-aurora"``."""
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", norm.lower()).strip("-")
    return slug[:max_len].strip("-") or "ecurie"


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def derived_seed(secret: str, *parts: Any, digits: int = 12) -> int:
    """``int(sha256(f"{secret}:{parts...}")[:digits hex])`` — championship seeds (docs/LEAGUE.md)."""
    key = ":".join([secret, *[str(p) for p in parts]])
    return int(sha256_hex(key)[:digits], 16)
