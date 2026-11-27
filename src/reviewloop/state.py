import hashlib
import json
import re
from typing import TypeVar

from pydantic import BaseModel

from reviewloop.models import FindingMarker, RunState

_FINDING_RE = re.compile(r"<!-- reviewloop:finding (.*?) -->")
_M = TypeVar("_M", bound=BaseModel)
_STATE_RE = re.compile(r"<!-- reviewloop:state (.*?) -->")


def fingerprint(path: str, rule: str | None, code: str) -> str:
    collapsed = " ".join(code.split())
    return hashlib.sha256(f"{path}\0{rule or ''}\0{collapsed}".encode()).hexdigest()


def _render(kind: str, model: BaseModel) -> str:
    payload = json.dumps(model.model_dump(mode="json"), separators=(",", ":"))
    # A raw "-->" inside a value would close the HTML comment early.
    safe = payload.replace("-->", "--\\u003e")
    return f"<!-- reviewloop:{kind} {safe} -->"


def render_finding_marker(m: FindingMarker) -> str:
    return _render("finding", m)


def render_state_marker(s: RunState) -> str:
    return _render("state", s)


def _parse(pattern: re.Pattern[str], model: type[_M], body: str) -> _M | None:
    m = pattern.search(body)
    if m is None:
        return None
    try:
        return model.model_validate(json.loads(m.group(1)))
    except (ValueError, RecursionError):
        return None


def parse_finding_marker(body: str) -> FindingMarker | None:
    return _parse(_FINDING_RE, FindingMarker, body)


def parse_state_marker(body: str) -> RunState | None:
    return _parse(_STATE_RE, RunState, body)
