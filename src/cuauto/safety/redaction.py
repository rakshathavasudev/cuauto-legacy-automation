"""Redaction of secrets and regulated data before anything is logged, persisted or sent to a model.

Layers (applied in order):
  1. registered secrets (credentials)              -> [SECRET]
  2. registered sensitive input values (member id) -> [input:member_id]
  3. pattern rules (SSN, card/account numbers, money, e-mail, phone)
  4. semantic rule: text whose table/form label is a sensitive label (Name, SSN ...)
     is replaced wholesale -> [PII]   (see `redact_item`)
Regexes alone cannot catch a person's name; the semantic rule is what makes this
workable on screens, because back-office UIs label their fields.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from ..util import norm_label

_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("ssn", re.compile(r"\b(?:\d{3}|X{3})-(?:\d{2}|X{2})-\d{4}\b"), "[SSN]"),
    ("card", re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[CARD]"),
    ("account", re.compile(r"\b\d{9,}\b"), "[ACCT]"),
    ("money", re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?|\b\d{1,3}(?:,\d{3})+(?:\.\d{2})?\b|\b\d+\.\d{2}\b"), "[AMOUNT]"),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL]"),
    ("phone", re.compile(r"\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b"), "[PHONE]"),
]


class Redactor:
    def __init__(self, sensitive_labels: Iterable[str] = ()):
        self._secrets: dict[str, str] = {}
        self._values: dict[str, str] = {}
        self.sensitive_labels = {norm_label(s) for s in sensitive_labels}

    def register_secret(self, value: str | None, label: str = "SECRET") -> None:
        if value and len(value) >= 3:
            self._secrets[value] = f"[{label}]"

    def register_value(self, value: str | None, label: str, replacement: str | None = None) -> None:
        if value and len(value) >= 3:
            self._values[value] = replacement or f"[input:{label}]"

    def text(self, s: str | None) -> str | None:
        if not s:
            return s
        for value, repl in sorted(self._secrets.items(), key=lambda kv: -len(kv[0])):
            s = s.replace(value, repl)
        for value, repl in sorted(self._values.items(), key=lambda kv: -len(kv[0])):
            # numeric ids hide inside URLs/tokens ("m%3D10001%26"): use digit boundaries for them
            b = r"\d" if value.isdigit() else r"\w"
            s = re.sub(rf"(?<!{b}){re.escape(value)}(?!{b})", repl, s)
        for _, rx, repl in _PATTERNS:
            s = rx.sub(repl, s)
        return s

    def obj(self, o: Any) -> Any:
        if isinstance(o, str):
            return self.text(o)
        if isinstance(o, dict):
            return {k: ("[SECRET]" if k.lower() in {"password", "token", "secret", "authorization"} else self.obj(v))
                    for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [self.obj(v) for v in o]
        return o

    def is_sensitive_context(self, ctx: dict | None) -> bool:
        ctx = ctx or {}
        return any(norm_label(ctx.get(k)) in self.sensitive_labels
                   for k in ("row_label", "column_header", "field_label") if ctx.get(k))

    def redact_item(self, text: str, ctx: dict | None) -> str:
        return "[PII]" if self.is_sensitive_context(ctx) else (self.text(text) or "")
