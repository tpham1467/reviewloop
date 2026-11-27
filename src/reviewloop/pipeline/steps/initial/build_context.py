import json

from reviewloop.diff import changed_lines
from reviewloop.models import Thread
from reviewloop.pipeline.agent import OVERVIEW_SANDBOX, SECURITY_SANDBOX, copy_sandbox, write_diff, write_input
from reviewloop.pipeline.context import RunContext
from reviewloop.stacks import glob_match, rules_for

# How the code-review skill's Security section applies; the same wording as the review prompt.
SECURITY_SETTING = {
    True: "a separate security step runs; skip the Security section of the code-review skill",
    False: "enabled for the code-review step; apply its Security section",
}


def _previous(threads: list[Thread]) -> str:
    items = [
        {"id": t.marker.id, "path": t.path, "line": t.line, "severity": t.marker.sev.value, "rule": t.marker.rule,
         "resolved": t.resolved, "text": t.body}
        for t in threads
        if t.marker is not None
    ]  # fmt: skip
    return json.dumps(items, indent=2)


class BuildContext:
    """Write the diff, PR context, rules and previous findings to `.reviewloop/` for the agents."""

    id = "build-context"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        pr, cfg = ctx.pr, ctx.config
        write_diff(ctx.sandbox, ctx.diffs, {c.path: c.reason for c in ctx.excluded})

        context = [
            f"# {pr.title}",
            "",
            f"Branches: `{pr.source_branch}` into `{pr.target_branch}`, head `{pr.head_sha}`.",
            "",
            "## Description",
            "",
            pr.body or "(empty)",
            "",
            "## Business context",
            "",
            ctx.context_summary or "No business context was found.",
            "",
            "## Settings",
            "",
            f"- language: {cfg.language}",
            f"- suggestions: {'enabled' if cfg.suggestions else 'disabled'}",
            f"- security: {SECURITY_SETTING[cfg.security.enabled]}",
        ]
        write_input(ctx.sandbox, "context.md", "\n".join(context) + "\n")

        for path in rules_for(ctx.stacks):
            write_input(ctx.sandbox, f"rules/{path.name}", path.read_text(encoding="utf-8"))
        changed = [fd.path for fd in ctx.reviewable()]
        for n, rule in enumerate(cfg.rules):
            if not any(glob_match(rule.path, p) for p in changed):
                continue
            text = ctx.vcs.read_file(rule.file, pr.target_branch)
            if text is None:
                ctx.log.warning("rule file %s not found on %s", rule.file, pr.target_branch)
                continue
            write_input(ctx.sandbox, f"rules/repo-{n + 1}.md", text)

        write_input(ctx.sandbox, "previous-findings.json", _previous(ctx.previous_threads))

        # Decided here, before the review group starts, so large-diff-review and initial-review never race on it.
        ctx.split_review = changed_lines(ctx.reviewable()) > cfg.large_diff_lines
        # Last: the parallel review group's copies must hold every input above.
        if ctx.reviewable():
            copy_sandbox(ctx, OVERVIEW_SANDBOX)
            if cfg.security.enabled:
                copy_sandbox(ctx, SECURITY_SANDBOX)
