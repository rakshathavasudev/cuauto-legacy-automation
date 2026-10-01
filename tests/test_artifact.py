import copy

import pytest
import yaml
from pydantic import ValidationError

from cuauto.schema.artifact import Capability

BASE = yaml.safe_load("""
schema: cuauto.capability/v1
id: legacy-corebank.demo
name: demo
version: 1
title: Demo
description: Look up {{member_id}}
app: {app_id: legacy-corebank, compatible_versions: '>=4.2,<5.0', recorded_on_tenant: t, recorded_on_version: 4.2.3}
side_effects: reversible
inputs: [{name: member_id, description: id, pattern: '[0-9]{5,8}', sensitivity: pii}]
outputs: [{name: bal, type: money, description: b, source: {kind: text, context: {row_label: S00}}}]
outcomes: [{code: RECORD_NOT_FOUND, detector: record_not_found, description: nf}]
steps:
  - {id: s1, intent: fill, action: fill, target: {role: textbox, name: Member Number}, value: '{{member_id}}',
     risk: reversible}
  - {id: s2, intent: open, action: click, target: {role: link, name: View, context: {row_label: '{{member_id}}'}}}
success: {all_of: [{text_present: [SUMMARY]}]}
provenance: {source_run_id: r, recorded_at: now, model: m, discovery_goal: g}
""")


def test_roundtrip_and_stable_hash():
    cap = Capability.model_validate(BASE)
    again = Capability.from_yaml(cap.to_yaml())
    assert again == cap and again.content_hash() == cap.content_hash()


def test_rejects_undeclared_template_var():
    bad = copy.deepcopy(BASE)
    bad["steps"][0]["value"] = "{{account}}"
    with pytest.raises(ValidationError, match="undeclared input"):
        Capability.model_validate(bad)


def test_side_effects_cannot_understate_risk():
    bad = copy.deepcopy(BASE)
    bad["steps"][1]["risk"] = "irreversible"
    with pytest.raises(ValidationError, match="understates"):
        Capability.model_validate(bad)


def test_unknown_fields_forbidden():
    bad = copy.deepcopy(BASE)
    bad["steps"][0]["selector"] = "#x"
    with pytest.raises(ValidationError):
        Capability.model_validate(bad)


def test_validate_args_and_tool_schema():
    cap = Capability.model_validate(BASE)
    assert cap.validate_args({"member_id": "10001"}) == []
    assert cap.validate_args({"member_id": "12ab"})
    assert cap.validate_args({}) == ["missing required input 'member_id'"]
    assert "unknown input 'x'" in cap.validate_args({"member_id": "10001", "x": "1"})
    ts = cap.tool_schema()
    assert ts["name"] == "legacy-corebank__demo" and ts["input_schema"]["required"] == ["member_id"]
