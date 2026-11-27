from reviewloop.models import Thread
from reviewloop.pipeline.context import RunContext
from reviewloop.security.scanners import scan_history

SECRET_RULE_PREFIX = "gitleaks:"  # noqa: S105 - a rule prefix, not a secret
STILL_FOUND = (
    "gitleaks still finds this secret in the commits of this pull request. Removing it in a later commit does not "
    "remove it from history: rotate the secret, then rewrite the branch history or ask a maintainer to resolve this."
)
NOT_RESCANNED = (
    "This secret finding could not be rescanned (gitleaks is not installed or failed), so it stays open: rotate the "
    "secret and have a maintainer resolve this thread."
)


def is_secret(thread: Thread) -> bool:
    """A thread for a secret: its rule is a gitleaks rule (ruling R38).

    Agent findings that confirm a scanner hit carry `<tool>:<rule>` as their rule (security-review prompt), and
    scanner-only findings always do, so the rule prefix covers both.
    """
    return thread.marker is not None and (thread.marker.rule or "").startswith(SECRET_RULE_PREFIX)


class SecurityRescan:
    """Rescan the PR's commits for secret threads instead of trusting a reply or a later deleting commit."""

    id = "security-rescan"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        assert ctx.follow_up is not None  # noqa: S101 - set by resolve-target-turn
        follow = ctx.follow_up
        secrets = {fid: t for fid, t in follow.threads.items() if is_secret(t)}
        if not secrets:
            return
        hits = scan_history(ctx.source_dir, ctx.pr.base_sha, ctx.pr.head_sha)
        for fid, thread in secrets.items():
            assert thread.marker is not None  # noqa: S101 - is_secret checks it
            if hits is None:
                follow.forced[fid] = NOT_RESCANNED
            elif any(f"{h.tool}:{h.rule}" == thread.marker.rule for h in hits):
                # Matched by rule, not path: a secret moved to another file is the same unrotated secret.
                follow.forced[fid] = STILL_FOUND
