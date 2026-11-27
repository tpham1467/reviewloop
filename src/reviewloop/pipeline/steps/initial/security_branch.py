import json
from dataclasses import asdict

from reviewloop.models import Finding, Severity
from reviewloop.pipeline import prompts
from reviewloop.pipeline.agent import SECURITY_SANDBOX, branch_sandbox, numbered, run_agent, write_input
from reviewloop.pipeline.context import RunContext
from reviewloop.security.scanners import ScanFinding, run_scanners
from reviewloop.skills.schemas import ReviewOutput, SecurityVerifyOutput

REVIEW_STEP = "security-review"
VERIFY_STEP = "security-verify"


def forced_finding(hit: ScanFinding) -> Finding:
    """A scanner hit posted as a general thread, outside the agents' reach (ruling R24).

    A secret committed in the PR's history stays there even when a later commit removes it, and a hit with no line
    cannot be anchored. `forced` keeps it out of security-verify, the gate and anchoring.
    """
    where = f"{hit.path}:{hit.line}" if hit.line else hit.path
    if hit.in_history:
        severity = Severity.CRITICAL
        text = (
            f"{hit.tool} found a secret ({hit.message or hit.rule}) committed in this PR's history at `{where}`. "
            "Removing it in a later commit does not remove it from history: rotate the secret."
        )
    else:
        severity = Severity.HIGH
        text = f"{hit.tool} reported `{hit.rule}` in `{where}` with no line: {hit.message or hit.rule}. Check the file."
    return Finding(
        path=hit.path,
        line=hit.line or 1,
        severity=severity,
        rule=f"{hit.tool}:{hit.rule}",
        category="security",
        text=text,
        existing_code="",
        security=True,
        forced=True,
    )


def _covers(agent: Finding, hit: Finding) -> bool:
    """The agent reported the same scanner rule on lines that include the hit's line."""
    if agent.path != hit.path or agent.rule != hit.rule:
        return False
    span = max(len(agent.existing_code.strip("\n").split("\n")), 1)
    return agent.line <= hit.line < agent.line + span


class SecurityBranch:
    """Scanners, then the security-review and security-verify agents, in parallel with the main review (spec 5.1)."""

    id = "security-branch"
    group: str | None = "review"

    def run(self, ctx: RunContext) -> None:
        security = ctx.config.security
        reviewable = ctx.reviewable()
        if not security.enabled or not reviewable:
            return
        paths = [fd.path for fd in reviewable]
        hits = run_scanners(security.scanners, ctx.source_dir, ctx.pr.base_sha, ctx.pr.head_sha, paths)
        forced = [forced_finding(h) for h in hits if h.in_history or h.line is None]

        sandbox = branch_sandbox(ctx, SECURITY_SANDBOX)
        # Every hit is also a hint, so the agent can report it with quoted code and a fix.
        write_input(sandbox, "security/scan.json", json.dumps([asdict(h) for h in hits], indent=2))
        language = ctx.config.language
        out = run_agent(ctx, REVIEW_STEP, ["security-review"], prompts.security_review(REVIEW_STEP, language),
                        ReviewOutput, sandbox)  # fmt: skip
        found = [f.model_copy(update={"security": True, "suggestion": None}) for f in out.findings]
        if found:
            write_input(sandbox, "security/findings.json", numbered(found))
            verdict = run_agent(ctx, VERIFY_STEP, ["security-verify"], prompts.security_verify(VERIFY_STEP, language),
                                SecurityVerifyOutput, sandbox)  # fmt: skip
            kept = set(verdict.kept_ids)
            found = [f for i, f in enumerate(found) if i in kept]
        # A secret the agent confirmed inline needs no second, general thread.
        forced = [h for h in forced if not any(_covers(f, h) for f in found)]
        ctx.security_findings = forced + found
