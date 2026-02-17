from polymarket_arb.wss_auth import build_user_subscribe_payload, redact_payload


def test_user_ws_payload_shape():
    payload = build_user_subscribe_payload("k", "s", "p")
    assert payload["channel"] == "user"
    assert payload["auth"]["apikey"] == "k"
    assert payload["auth"]["secret"] == "s"
    assert payload["auth"]["passphrase"] == "p"


def test_redaction():
    payload = build_user_subscribe_payload("k", "s", "p")
    redacted = redact_payload(payload)
    assert redacted["auth"]["apikey"] == "***REDACTED***"
    assert redacted["auth"]["secret"] == "***REDACTED***"
    assert redacted["auth"]["passphrase"] == "***REDACTED***"
