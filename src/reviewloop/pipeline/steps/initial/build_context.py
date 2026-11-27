import json

from reviewloop.diff import FileDiff
from reviewloop.models import Thread
from reviewloop.pipeline.agent import OVERVIEW_SANDBOX, SECURITY_SANDBOX, copy_sandbox, write_input
from reviewloop.pipeline.context import RunContext
from reviewloop.stacks import glob_match, rules_for

# How the code-review skill's Security section applies; the same wording as the review prompt.
SECURITY_SETTING = {
    True: "a separate security step runs; skip the Security section of the code-review skill",
    False: "enabled for the code-review step; apply its Security section",
}
_PREFIX = {"add": "+", "del": "-", "ctx": " "}


def render_diff(fd: FileDiff) -> str:
    head = f"--- {fd.old_path or fd.path}\n+++ {fd.path}\n"
    hunks = [
        f"@@ -{h.old_start} +{h.new_start} @@\n" + "".join(f"{_PREFIX[ln.kind]}{ln.text}\n" for ln in h.lines)
        for h in fd.hunks
    ]
    return head + "".join(hunks)


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
        excluded = {c.path: c.reason for c in ctx.excluded}
        index = ["# Diff index", "", "| File | Status | Input |", "| --- | --- | --- |"]
        for fd in ctx.diffs:
            reason = excluded.get(fd.path)
            where = f"excluded: {reason}" if reason else f"`.reviewloop/diff/files/{fd.path}.diff`"
            index.append(f"| `{fd.path}` | {fd.status} | {where} |")
            if reason is None:
                write_input(ctx.sandbox, f"diff/files/{fd.path}.diff", render_diff(fd))
        write_input(ctx.sandbox, "diff/index.md", "\n".join(index) + "\n")

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

        # Last: the parallel review group's copies must hold every input above.
        if ctx.reviewable():
            copy_sandbox(ctx, OVERVIEW_SANDBOX)
            if cfg.security.enabled:
                copy_sandbox(ctx, SECURITY_SANDBOX)
