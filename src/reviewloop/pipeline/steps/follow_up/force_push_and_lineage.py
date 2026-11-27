from reviewloop.errors import ReviewloopError
from reviewloop.output.redact import redact
from reviewloop.pipeline.context import RunContext
from reviewloop.pipeline.git import is_sha, run_git

# `git merge-base --is-ancestor` exits 0 for an ancestor and 1 for a commit that is not one.
_NOT_ANCESTOR = 1


class ForcePushAndLineage:
    """Diff this turn from the previous head when it is still an ancestor of the head; otherwise, after a force push
    or a rebase, from the merge-base with the target branch, as the initial review does (ruling R36)."""

    id = "force-push-and-lineage"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        assert ctx.previous_state is not None and ctx.follow_up is not None  # noqa: S101 - set by resolve-target-turn
        previous, head = ctx.previous_state.sha, ctx.pr.head_sha
        if not (is_sha(previous) and is_sha(head)):
            # The marker is untrusted input: a value that is not a SHA never reaches git.
            ctx.log.warning("previous head in the state marker is not a commit SHA; diffing from the merge-base")
            return
        result = run_git(ctx.source_dir, "merge-base", "--is-ancestor", previous, head)
        if result.returncode == 0:
            ctx.follow_up.diff_base = previous
        elif result.returncode == _NOT_ANCESTOR or b"not a valid" in result.stderr.lower():
            # A force push may also have dropped the previous head from the checkout altogether.
            ctx.log.info("previous head %s is not an ancestor of %s: force push, diffing from the merge-base",
                         previous, head)  # fmt: skip
        else:
            ctx.log.warning("git merge-base failed: %s", redact(result.stderr.decode(errors="replace").strip()))
            raise ReviewloopError("force-push-and-lineage: git merge-base failed in the CI checkout")
