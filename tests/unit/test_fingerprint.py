from webhook_gateway.services.events import request_fingerprint


def test_fingerprint_ignores_key_order() -> None:
    assert request_fingerprint("t", {"a": 1, "b": [1, {"c": 2, "d": 3}]}) == request_fingerprint(
        "t", {"b": [1, {"d": 3, "c": 2}], "a": 1}
    )


def test_fingerprint_depends_on_type_and_payload() -> None:
    base = request_fingerprint("t", {"a": 1})
    assert base != request_fingerprint("u", {"a": 1})
    assert base != request_fingerprint("t", {"a": 2})
    assert request_fingerprint("t", [1, 2]) != request_fingerprint("t", [2, 1])
