from reviewloop.pipeline.context import RunContext
from reviewloop.pipeline.findings import finding_fingerprint


class SecurityMerge:
    """Add the verified security findings; one already reported by the main review (same fingerprint) is skipped."""

    id = "security-merge"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        seen = {finding_fingerprint(f) for f in ctx.findings}
        for f in ctx.security_findings:
            fp = finding_fingerprint(f)
            if fp not in seen:
                seen.add(fp)
                ctx.findings.append(f)
