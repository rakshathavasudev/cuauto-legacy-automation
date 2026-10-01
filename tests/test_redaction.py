from cuauto.safety.redaction import Redactor


def test_patterns_and_registered_values():
    r = Redactor(["Member Name", "SSN"])
    r.register_secret("demo-pass-123")
    r.register_value("10001", "member_id")
    s = r.text("pw demo-pass-123 member 10001 in /cgi/mbrdtl?m=10001&tok=ab url m%3D10001%26 bal $1,234.56 "
               "raw 1234.56 ssn 123-45-6789 card 4111 1111 1111 1111 mail a.b@x.org")
    for leaked in ("demo-pass-123", "10001", "1,234.56", "1234.56", "123-45-6789", "4111", "a.b@x.org"):
        assert leaked not in s, leaked
    assert "[input:member_id]" in s and "[SECRET]" in s and "[AMOUNT]" in s


def test_does_not_eat_neighbouring_numbers():
    r = Redactor()
    r.register_value("10001", "member_id")
    assert r.text("110001 100011") == "110001 100011"


def test_semantic_label_redaction():
    r = Redactor(["Member Name"])
    assert r.redact_item("JANE Q DOE", {"row_label": "Member Name"}) == "[PII]"
    assert r.redact_item("ACTIVE", {"column_header": "Status"}) == "ACTIVE"


def test_obj_masks_secret_keys():
    assert Redactor().obj({"password": "x", "nested": [{"token": "y"}]}) == {"password": "[SECRET]",
                                                                          "nested": [{"token": "[SECRET]"}]}
