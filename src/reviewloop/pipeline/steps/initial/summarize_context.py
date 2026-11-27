import re

from reviewloop.pipeline import prompts
from reviewloop.pipeline.agent import run_agent, write_input
from reviewloop.pipeline.context import RunContext
from reviewloop.pipeline.engine import StopFlow
from reviewloop.skills.schemas import ContextOutput

STEP = "summarize-context"
NO_CONTEXT = "Review skipped: no business context found, and the config sets tracker.require_context."
# What get_tracker needs from the environment, per tracker type.
CREDENTIAL_ENV = {
    "github": ("GITHUB_TOKEN", "GITHUB_REPOSITORY"),
    "gitlab": ("CI_SERVER_URL", "CI_PROJECT_ID", "REVIEWLOOP_GITLAB_TOKEN"),
    "jira": ("REVIEWLOOP_JIRA_TOKEN", "REVIEWLOOP_JIRA_EMAIL"),
}


def _no_tracker(ctx: RunContext, kind: str) -> str:
    missing = [name for name in CREDENTIAL_ENV[kind] if not ctx.env.get(name)]
    if kind == "jira" and "REVIEWLOOP_JIRA_EMAIL" in missing:
        missing[missing.index("REVIEWLOOP_JIRA_EMAIL")] = "REVIEWLOOP_JIRA_EMAIL (Jira Cloud only)"
    names = ", ".join(missing) or "tracker.url"
    return f"Review skipped: tracker.require_context is set but the {kind} tracker has no credentials; set {names}."


class SummarizeContext:
    """Agent: business context from the tickets; with none and `require_context`, stop with a notice."""

    id = STEP
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        tracker = ctx.config.tracker
        required = tracker is not None and tracker.require_context
        if not ctx.tickets:
            if required and tracker is not None and ctx.tracker is None:
                raise StopFlow(_no_tracker(ctx, tracker.type))
            if required:
                raise StopFlow(NO_CONTEXT)
            return
        for ticket in ctx.tickets:
            name = re.sub(r"[^A-Za-z0-9_.-]", "_", ticket.key)
            write_input(
                ctx.sandbox, f"tickets/{name}.md", f"# {ticket.key}: {ticket.title}\n\n{ticket.url}\n\n{ticket.body}\n"
            )
        write_input(ctx.sandbox, "context.md", f"# {ctx.pr.title}\n\n{ctx.pr.body}\n")
        out = run_agent(ctx, STEP, ["summarize-context"], prompts.summarize_context(STEP, ctx.config.language),
                        ContextOutput)  # fmt: skip
        if required and not out.has_context:
            raise StopFlow(NO_CONTEXT)
        ctx.context_summary = out.summary if out.has_context else ""
