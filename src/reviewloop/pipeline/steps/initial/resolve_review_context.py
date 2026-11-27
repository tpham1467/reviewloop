from reviewloop.pipeline.context import RunContext
from reviewloop.trackers.registry import extract_keys

# Used when `tracker.key_pattern` is unset.
DEFAULT_KEY_PATTERNS = {"jira": r"\b[A-Z][A-Z0-9]+-\d+\b", "github": r"#\d+", "gitlab": r"#\d+"}


class ResolveReviewContext:
    """Find ticket keys in the PR title, branch and body and fetch the tickets."""

    id = "resolve-review-context"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        tracker = ctx.config.tracker
        if tracker is None or ctx.tracker is None:
            return
        pattern = tracker.key_pattern or DEFAULT_KEY_PATTERNS[tracker.type]
        keys = extract_keys(f"{ctx.pr.title}\n{ctx.pr.source_branch}\n{ctx.pr.body}", pattern)
        if keys:
            ctx.tickets = ctx.tracker.fetch(keys)
