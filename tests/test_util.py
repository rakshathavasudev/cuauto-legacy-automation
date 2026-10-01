from cuauto.util import render, template_vars, version_in_range


def test_version_ranges():
    assert version_in_range("4.2.3", ">=4.2,<5.0")
    assert version_in_range("4.3.0", ">=4.2,<5.0")
    assert not version_in_range("5.0.1", ">=4.2,<5.0")
    assert not version_in_range("4.1.9", ">=4.2,<5.0")


def test_templates():
    assert template_vars("{{a}} and {{ b }}") == ["a", "b"] or set(template_vars("{{a}} and {{ b }}")) == {"a", "b"}
    assert render("m={{member_id}}", {"member_id": "1"}) == "m=1"
