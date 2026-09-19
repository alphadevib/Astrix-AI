"""Regression: session tokens must never reach the server log."""

from __future__ import annotations

import logging

from backend.app.main import _RedactTokens


def test_websocket_tokens_are_redacted_from_logs():
    secret = "s3cr3t-Session_Token-abcdef0123456789"
    record = logging.LogRecord(
        "uvicorn.error",
        logging.INFO,
        __file__,
        1,
        '%s - "WebSocket %s" [accepted]',
        (("127.0.0.1", 5000), f"/ws/telemetry?token={secret}&x=1"),
        None,
    )
    assert _RedactTokens().filter(record)
    message = record.getMessage()
    assert secret not in message
    assert "token=[redacted]&x=1" in message
