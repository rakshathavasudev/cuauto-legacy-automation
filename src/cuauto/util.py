"""Small shared helpers: ids, time, label normalisation, templating, version ranges."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import uuid
from typing import Any, Mapping

_WS = re.compile(r"\s+")
_TEMPLATE = re.compile(r"\{\{\s*([a-zA-Z_][\w.]*)\s*\}\}")


def utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def norm_label(s: str | None) -> str:
    """Normalise a human-visible label for comparison: case, whitespace, trailing ':'/'*'."""
    s = _WS.sub(" ", s or "").strip().lower()
    return s.rstrip(":*").strip()


def render(template: str, values: Mapping[str, Any]) -> str:
    def rep(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in values:
            raise KeyError(f"no value for template variable '{key}'")
        return str(values[key])

    return _TEMPLATE.sub(rep, template)


def template_vars(s: str | None) -> list[str]:
    return _TEMPLATE.findall(s or "")


def sha256_json(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", v))


def version_in_range(version: str, spec: str) -> bool:
    """Tiny comparator for specs like '>=4.2,<5.0' (enough for vendor product versions)."""
    v = _vtuple(version)
    for clause in filter(None, (c.strip() for c in spec.split(","))):
        m = re.match(r"(>=|<=|==|>|<)\s*(.+)", clause)
        if not m:
            raise ValueError(f"bad version clause: {clause}")
        op, rhs = m.group(1), _vtuple(m.group(2))
        n = max(len(v), len(rhs))
        a, b = v + (0,) * (n - len(v)), rhs + (0,) * (n - len(rhs))
        ok = {">=": a >= b, "<=": a <= b, "==": a == b, ">": a > b, "<": a < b}[op]
        if not ok:
            return False
    return True
