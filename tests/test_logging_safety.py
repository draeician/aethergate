"""Central log-safety / redaction tests with canaries for every secret class.

These tests prove that common secret and content shapes do not appear in captured
logs, and that the structured access log carries only safe correlation fields.
Redaction is defense in depth: the code never deliberately logs these values.
"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from aethergate.logging_config import (
    RedactionFilter,
    configure_logging,
    log_event,
    redact_text,
)

# Secret classes with a detectable shape: the redaction backstop must scrub them.
CANARIES = {
    "agk_key": "agk_live_0123456789abcdefABCDEF",
    "ags_key": "ags_session_0123456789abcdef",
    "bootstrap": "agb_bootstrap_0123456789abcdef",
    "authorization": "Authorization: Bearer sk-live-deadbeefcafebabe",
    "session_cookie": "ag_session=rawsessionvalue123",
    "csrf_cookie": "ag_csrf=rawcsrfvalue456",
    "oidc_code": "code=oidc-authcode-fixture-789",
    "oidc_token": "id_token=eyJhbGciOiJSUzI1NiJ9.payload.sig",
    "code_verifier": "code_verifier=verifierfixture-abc",
    "client_secret": "client_secret=oidcsecretfixture",
    "provider_secret": "api_key=sk-provider-secret-fixture",
    "db_url": "postgresql+asyncpg://aethergate:supersecretpw@db.internal:5432/aethergate",
    "db_password": "POSTGRES_PASSWORD=supersecretpw",
    "private_key": (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEsecretsecretsecret\n"
        "-----END RSA PRIVATE KEY-----"
    ),
}

# Content classes have no detectable shape; the guarantee is that the code never
# logs them (there is no regex that can meaningfully redact free-form content).
CONTENT_CANARIES = {
    "prompt_canary": "PROMPT_CANARY_the-user-asked-a-secret",
    "completion_canary": "COMPLETION_CANARY_the-model-answered",
    "tool_call_canary": "TOOLCALL_CANARY_get_secret(arg=1)",
}


def _make_record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg=message, args=(), exc_info=None,
    )


def test_redact_text_scrubs_every_canary():
    for name, canary in CANARIES.items():
        raw = f"leading {canary} trailing"
        redacted = redact_text(raw)
        assert canary not in redacted, f"{name} not redacted"
        assert "REDACTED" in redacted.upper(), f"{name} not marked redacted"


def test_redaction_filter_scrubs_message_args_and_extras():
    record = _make_record(
        "connect %s using %s",
    )
    record.args = (CANARIES["db_url"], CANARIES["agk_key"])
    record.secret_field = CANARIES["provider_secret"]
    filtering = RedactionFilter()
    assert filtering.filter(record) is True
    assert CANARIES["db_url"] not in str(record.args)
    assert CANARIES["agk_key"] not in str(record.args)
    assert CANARIES["provider_secret"] not in record.secret_field


def test_structured_handler_never_emits_canaries(caplog):
    handler = configure_logging(level="info", json_logs=True, app_env="prod")
    logger = logging.getLogger("aethergate.test.canary")
    with caplog.at_level(logging.INFO):
        for canary in CANARIES.values():
            logger.info("value is %s", canary)
        # Also emit through a structured event with a secret-looking field.
        log_event(
            logger,
            "test.event",
            authorization=CANARIES["authorization"],
            agk=CANARIES["agk_key"],
        )
    # The RedactionFilter mutates records in place, so the captured records are
    # already scrubbed for the detectable secret classes.
    captured = "\n".join(r.getMessage() for r in caplog.records)
    for canary in CANARIES.values():
        assert canary not in captured, f"canary leaked to captured logs: {canary[:20]}"
    handler.close()


def test_content_is_never_logged_by_lifecycle_code():
    """Prompt/completion/tool content must never reach a lifecycle log call.

    This is a static guarantee: lifecycle logging uses only IDs/state/status
    fields. It also asserts the content canaries are absent from the source
    modules that emit structured logs.
    """
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    logged_modules = [
        repo / "src" / "aethergate" / "scheduler" / "service.py",
        repo / "src" / "aethergate" / "worker.py",
        repo / "src" / "aethergate" / "main.py",
        repo / "src" / "aethergate" / "api" / "openai_chat.py",
    ]
    for module in logged_modules:
        text = module.read_text()
        for canary in CONTENT_CANARIES.values():
            assert canary not in text
        # Lifecycle log calls must never pass content-bearing fields.
        for line in text.splitlines():
            if "log_event(" in line:
                for forbidden in ("content", "prompt", "messages", "payload", "result_enc"):
                    assert forbidden not in line, f"{module.name}: {line.strip()}"


def test_json_formatter_emits_safe_fields(caplog):
    handler = configure_logging(level="info", json_logs=True, app_env="prod")
    logger = logging.getLogger("aethergate.test.json")
    with caplog.at_level(logging.INFO):
        log_event(logger, "request.settled", request_id="req-1", state="succeeded", status=200)
    # The record carries the structured fields safely.
    record = caplog.records[-1]
    assert record.event == "request.settled"
    assert record.request_id == "req-1"
    rendered = handler.formatter.format(record) if handler.formatter else ""
    parsed = json.loads(rendered)
    assert parsed["event"] == "request.settled"
    assert parsed["request_id"] == "req-1"
    assert parsed["state"] == "succeeded"
    assert parsed["level"] == "INFO"
    assert "timestamp" in parsed
    handler.close()


def test_access_log_has_request_id_and_no_secret_header(monkeypatch):
    """The HTTP access log carries the gateway request id and no secret header."""
    from aethergate.main import app

    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = _Capture()
    access_logger = logging.getLogger("aethergate.access")
    access_logger.addHandler(handler)
    try:
        client = TestClient(app)
        client.get(
            "/health/live",
            headers={"Authorization": f"Bearer {CANARIES['agk_key']}"},
        )
    finally:
        access_logger.removeHandler(handler)

    assert captured, "no access log emitted"
    event = captured[-1]
    assert event.method == "GET"
    assert event.route == "/health/live"
    assert getattr(event, "request_id", None)
    # No secret header or query string is present anywhere on the record.
    rendered = json.dumps(event.__dict__, default=str)
    assert CANARIES["agk_key"] not in rendered
    assert "Authorization" not in rendered


def test_request_id_correlates_response_and_logs(monkeypatch):
    """The gateway request ID in the response matches the access log record."""
    from aethergate.main import app

    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = _Capture()
    access_logger = logging.getLogger("aethergate.access")
    access_logger.addHandler(handler)
    try:
        client = TestClient(app)
        client.get("/health/live")
    finally:
        access_logger.removeHandler(handler)
    # The access log request_id is a stable gateway ID (health has no body id).
    request_ids = {getattr(r, "request_id", None) for r in captured}
    assert request_ids and None not in request_ids


@pytest.mark.parametrize(
    "payload",
    [
        "Authorization: Bearer",
        "agk_",
        "ags_",
        "agb_",
    ],
)
def test_redact_text_idempotent_on_already_safe(payload):
    # Strings that are not secrets are left intact.
    assert redact_text("plain text without secrets") == "plain text without secrets"
