import logging
import os

from polymarket_arb.logging_utils import RedactionFilter


def test_log_redaction_filter_masks_sensitive_fields(monkeypatch):
    monkeypatch.setenv("CLOB_API_KEY", "mykey")
    monkeypatch.setenv("CLOB_API_SECRET", "mysecret")
    f = RedactionFilter()

    record = logging.LogRecord(
        name="x",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='auth={"apikey":"mykey","secret":"mysecret","passphrase":"p"}',
        args=(),
        exc_info=None,
    )

    ok = f.filter(record)
    assert ok
    msg = str(record.msg)
    assert "mykey" not in msg
    assert "mysecret" not in msg
    assert "***REDACTED***" in msg
