import re

from reviewloop.models import COMMAND_PREFIX, Finding

_PLACEHOLDER = "[redacted]"

_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"github_pat_[A-Za-z0-9_]{50,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"),
]
# The first non-empty line, when it starts with the command word: posted, it would read as a command.
_LEADING_COMMAND = re.compile(rf"\A(\s*){re.escape(COMMAND_PREFIX)}")
_PASSWORD = re.compile(r"""(password"?\s*[:=]\s*)("[^"]*"|'[^']*'|\S+)""", re.IGNORECASE)


def redact(text: str) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub(_PLACEHOLDER, text)
    return _PASSWORD.sub(lambda m: m.group(1) + _PLACEHOLDER, text)


def safe_text(text: str) -> str:
    """Redact secrets and neutralize HTML comments and a leading command, so free text can neither leak a secret,
    forge a marker nor start a reviewloop run when the bot posts it (the bot's own notes fire comment webhooks)."""
    text = redact(text).replace("<!--", "&lt;!--").replace("-->", "--&gt;")
    return _LEADING_COMMAND.sub(lambda m: f"{m.group(1)}`{COMMAND_PREFIX}`", text, count=1)


def redact_finding(f: Finding) -> Finding:
    return f.model_copy(
        update={
            "text": redact(f.text),
            "existing_code": redact(f.existing_code),
            "cause_code": None if f.cause_code is None else redact(f.cause_code),
            "suggestion": None if f.suggestion is None else redact(f.suggestion),
        }
    )
