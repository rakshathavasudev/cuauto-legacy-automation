"""The capability artifact: a typed, versioned, reviewable contract an AI agent can invoke.

Design notes (see REPORT.md §2):
  * Targets are *semantic* (role + visible name + table/form context) rather than DOM
    selectors, so the same schema describes a web page, a legacy frameset or a desktop
    accessibility tree. Surface-specific selectors live only in `fallbacks`.
  * Every step carries a `risk` class and an `expect` checkpoint; replay waits for the
    expected state rather than for time.
  * Values are templates (`{{member_id}}`); concrete recorded values never appear.
  * The artifact is immutable content. Lifecycle (draft/approved) is registry metadata in
    the DB, so approving a capability never changes its content hash.
"""
from __future__ import annotations

import re
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..util import sha256_json, template_vars

SCHEMA_ID = "cuauto.capability/v1"

Sensitivity = Literal["public", "internal", "pii", "financial", "secret"]
Risk = Literal["safe", "reversible", "irreversible"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TargetContext(_M):
    frame: str | None = Field(None, description="Frame/window hint (e.g. 'main'). A hint, not a hard filter.")
    row_label: str | None = Field(None, description="First cell of the table row the target sits in.")
    column_header: str | None = Field(None, description="Header of the table column the target sits in.")
    field_label: str | None = Field(None, description="Visible label adjacent to the target (legacy forms).")


class Fallback(_M):
    strategy: Literal["xpath", "css", "coordinates", "automation_id"]
    value: str
    note: str | None = None


class Target(_M):
    kind: Literal["control", "text"] = "control"
    role: str | None = Field(None, description="Semantic role: button, link, textbox, combobox, text ...")
    name: str | None = Field(None, description="Accessible/visible name, as an operator would read it.")
    context: TargetContext = Field(default_factory=TargetContext)
    nth: int | None = Field(None, description="Index among semantic matches; only set when the name is ambiguous.")
    fallbacks: list[Fallback] = Field(default_factory=list,
                                      description="Surface-specific last resort; using one is reported as drift.")


class Expectation(_M):
    """A checkpoint: a state that must be observed, polled until timeout."""
    frame: str | None = None
    url_pattern: str | None = Field(None, description="Glob on the frame URL path, e.g. /cgi/mbrdtl*")
    text_present: list[str] = Field(default_factory=list)
    text_absent: list[str] = Field(default_factory=list)
    timeout_ms: int = 8000


class Step(_M):
    id: str
    intent: str = Field(description="Why this step exists, in plain words (for reviewers).")
    action: Literal["click", "fill", "select", "press"]
    target: Target
    value: str | None = Field(None, description="Template; may reference {{inputs}} only.")
    risk: Risk = "safe"
    expect: Expectation | None = None
    origin: Literal["model", "human", "profile"] = "model"


class ParamSpec(_M):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    type: Literal["string", "integer", "decimal", "enum"] = "string"
    description: str
    pattern: str | None = None
    enum: list[str] | None = None
    required: bool = True
    sensitivity: Sensitivity = "internal"


class OutputSpec(_M):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    type: Literal["string", "integer", "decimal", "money", "date", "boolean"] = "string"
    description: str
    source: Target
    sensitivity: Sensitivity = "internal"
    required: bool = True


class OutcomeSpec(_M):
    """A business outcome the caller must handle (not an error)."""
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    detector: str
    description: str


class SuccessCondition(_M):
    all_of: list[Expectation]
    require_outputs: bool = True


class AppRef(_M):
    app_id: str
    surface: Literal["web", "legacy_web", "desktop"] = "legacy_web"
    compatible_versions: str = Field(description="Vendor product version range, e.g. '>=4.2,<5.0'")
    recorded_on_tenant: str
    recorded_on_version: str


class EntryPoint(_M):
    path: str = "/"


class Provenance(_M):
    source_run_id: str
    recorded_at: str
    model: str
    discovery_goal: str
    human_steps: int = 0


class Capability(_M):
    schema_: str = Field(SCHEMA_ID, alias="schema")
    id: str = Field(description="<app_id>.<name>; stable across versions")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: int = Field(ge=1)
    title: str
    description: str = Field(description="What the capability does, written for a calling agent.")
    app: AppRef
    requires_auth: bool = True
    entry: EntryPoint = Field(default_factory=EntryPoint)
    side_effects: Risk = "safe"
    inputs: list[ParamSpec]
    outputs: list[OutputSpec]
    outcomes: list[OutcomeSpec]
    steps: list[Step]
    success: SuccessCondition
    provenance: Provenance

    @model_validator(mode="after")
    def _check(self) -> "Capability":
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step ids must be unique")
        declared = {p.name for p in self.inputs}
        for s in self.steps:
            for field in (s.value, s.target.name, s.target.context.row_label):
                for var in template_vars(field):
                    if var not in declared:
                        raise ValueError(f"step {s.id} references undeclared input '{var}'")
        worst = max((s.risk for s in self.steps), key=["safe", "reversible", "irreversible"].index, default="safe")
        if ["safe", "reversible", "irreversible"].index(worst) > ["safe", "reversible", "irreversible"].index(
                self.side_effects):
            raise ValueError(f"side_effects='{self.side_effects}' understates step risk '{worst}'")
        for p in self.inputs:
            if p.pattern:
                re.compile(p.pattern)
        return self

    # -- serialisation -------------------------------------------------------------
    def to_dict(self) -> dict:
        return self.model_dump(by_alias=True, exclude_none=True, mode="json")

    def content_hash(self) -> str:
        return sha256_json(self.to_dict())

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True, width=110)

    @classmethod
    def from_yaml(cls, text: str) -> "Capability":
        return cls.model_validate(yaml.safe_load(text))

    def validate_args(self, args: dict[str, str]) -> list[str]:
        """Contract check before touching the UI. Returns human-readable errors."""
        errs: list[str] = []
        known = {p.name for p in self.inputs}
        for extra in sorted(set(args) - known):
            errs.append(f"unknown input '{extra}'")
        for p in self.inputs:
            v = args.get(p.name)
            if v is None or v == "":
                if p.required:
                    errs.append(f"missing required input '{p.name}'")
                continue
            if p.type == "integer" and not re.fullmatch(r"-?\d+", v):
                errs.append(f"input '{p.name}' must be an integer")
            if p.type == "decimal" and not re.fullmatch(r"-?\d+(\.\d+)?", v):
                errs.append(f"input '{p.name}' must be a decimal")
            if p.type == "enum" and p.enum and v not in p.enum:
                errs.append(f"input '{p.name}' must be one of {p.enum}")
            if p.pattern and not re.fullmatch(p.pattern, v):
                errs.append(f"input '{p.name}' does not match pattern {p.pattern}")
        return errs

    def tool_schema(self) -> dict:
        """JSON-schema function definition for agent-facing invocation."""
        type_map = {"string": "string", "integer": "string", "decimal": "string", "enum": "string"}
        props = {}
        for p in self.inputs:
            prop: dict = {"type": type_map[p.type], "description": p.description}
            if p.pattern:
                prop["pattern"] = p.pattern
            if p.enum:
                prop["enum"] = p.enum
            props[p.name] = prop
        return {
            "name": self.id.replace(".", "__"),
            "description": f"{self.title}. {self.description} Returns outputs "
                           f"{[o.name for o in self.outputs]} or a business outcome in "
                           f"{[o.code for o in self.outcomes]}. Side effects: {self.side_effects}.",
            "input_schema": {"type": "object", "properties": props,
                             "required": [p.name for p in self.inputs if p.required]},
        }
