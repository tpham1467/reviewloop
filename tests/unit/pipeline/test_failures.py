from reviewloop.errors import AgentStepError
from reviewloop.pipeline.failures import MAX_REASON, failure_reason

TOKEN = "ghp_" + "A1b2C3d4E5" * 4
KEY_BODY = "MIIEowIBAAKCAQEAFAKEFAKEFAKE"
PEM = f"-----BEGIN RSA PRIVATE KEY-----\n{KEY_BODY}\n{'Q' * 64}\n-----END RSA PRIVATE KEY-----"


def test_redaction_runs_before_truncation() -> None:
    # Each secret starts before the cut and ends after it; cut first, no pattern would match what is left.
    for secret in (TOKEN, PEM):
        message = "x" * (MAX_REASON - 20) + " stderr: " + secret + " tail"
        assert len(message) > MAX_REASON + len(secret) // 2

        reason = failure_reason("group 1", AgentStepError(message))

        assert "[redacted]" in reason
        assert "A1b2C3d4" not in reason
        assert KEY_BODY[:8] not in reason
        assert "BEGIN" not in reason
        assert len(reason) <= len("group 1: ") + MAX_REASON
        assert "\n" not in reason


def test_unexpected_error_hides_its_message() -> None:
    assert failure_reason("summary", KeyError(TOKEN)) == "summary: internal error (KeyError)"
