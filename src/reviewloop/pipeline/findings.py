"""Rules about findings that more than one step applies."""

from reviewloop.models import Finding
from reviewloop.state import fingerprint


def is_forced(f: Finding) -> bool:
    """A scanner hit posted as a general thread; security-verify, the gate and anchoring never see it (R24, R33)."""
    return f.forced


def finding_fingerprint(f: Finding) -> str:
    # A forced finding has no quote; its scanner rule, path and line are the stable key.
    return fingerprint(f.path, f.rule, f"scanner line {f.line}" if f.forced else f.existing_code)
