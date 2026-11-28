import json

from reviewloop.anchor import resolve_anchor
from reviewloop.models import Anchor, Finding, FindingMarker
from reviewloop.output.redact import redact, safe_text
from reviewloop.pipeline import prompts
from reviewloop.pipeline.agent import Reader, run_agent, sandbox_reader, write_input
from reviewloop.pipeline.context import RunContext
from reviewloop.pipeline.engine import StopFlow
from reviewloop.pipeline.findings import finding_fingerprint, is_forced
from reviewloop.skills.schemas import AnchorRepairOutput
from reviewloop.state import render_finding_marker

REPAIR_STEP = "anchor-repair"
REPAIR_FILE = "anchor-repair.json"
# Fields the anchor repair may change; the claim itself (text, severity, category) stays the agent's first answer.
_PLACEMENT = ("path", "line", "side", "existing_code", "cause_code")
_REPAIR_INPUT = {"path", "line", "side", "severity", "category", "text", "existing_code", "cause_code"}
ID_HEX = 6

Placed = list[tuple[Finding, Anchor | None]]


def _finding_id(fp: str, taken: set[str]) -> str:
    """`f-` + the shortest fingerprint prefix (6 hex, then 8, 10, ...) no other finding of this run uses."""
    size = ID_HEX
    while f"f-{fp[:size]}" in taken and size < len(fp):
        size += 2
    return f"f-{fp[:size]}"


class PostComment:
    """Anchor each finding (spec 7), drop repeats of earlier findings by fingerprint, post inline or general."""

    id = "post-comment"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        ctx.posting_started = True
        read = sandbox_reader(ctx.sandbox)
        placed: Placed = [(f, None if is_forced(f) else resolve_anchor(f, ctx.diffs, read)) for f in ctx.findings]
        lost = [i for i, (f, a) in enumerate(placed) if a is None and not is_forced(f)]
        if lost:
            placed = self._repair(ctx, placed, lost, read)
        # Checked right before posting (R25.4): the repair call above can take minutes.
        if ctx.overtaken(ctx.current_state()):
            ctx.log.info("another run advanced the turn; not posting")
            raise StopFlow()

        seen = {t.marker.fp for t in ctx.previous_threads if t.marker is not None}
        taken = {t.marker.id for t in ctx.previous_threads if t.marker is not None}
        for f, anchor in placed:
            fp = finding_fingerprint(f)
            if fp in seen:
                continue
            seen.add(fp)
            fid = _finding_id(fp, taken)
            taken.add(fid)
            self._post(ctx, f, fp, fid, anchor)

    def _repair(self, ctx: RunContext, placed: Placed, lost: list[int], read: Reader) -> Placed:
        """One agent turn for the findings whose quotes were not found; answers match the input by position."""
        inputs = [placed[i][0] for i in lost]
        files = sorted({f.path for f in inputs})
        payload = {"files": files, "findings": [f.model_dump(mode="json", include=_REPAIR_INPUT) for f in inputs]}
        write_input(ctx.sandbox, REPAIR_FILE, json.dumps(payload, indent=2))
        prompt = prompts.anchor_repair(REPAIR_STEP, ctx.config.language, f".reviewloop/{REPAIR_FILE}")
        out = run_agent(ctx, REPAIR_STEP, ["output-contract"], prompt, AnchorRepairOutput)
        if len(out.findings) != len(inputs):
            ctx.log.warning("anchor repair returned %d findings for %d; ignored", len(out.findings), len(inputs))
            return placed
        result = list(placed)
        for i, answer in zip(lost, out.findings, strict=True):
            if not answer.existing_code.strip():
                continue  # the agent did not find the code; a blank quote must not fall back to the line
            fixed = placed[i][0].model_copy(update={k: getattr(answer, k) for k in _PLACEMENT})
            anchor = resolve_anchor(fixed, ctx.diffs, read)
            if anchor is not None:
                result[i] = (fixed, anchor)
        return result

    def _post(self, ctx: RunContext, f: Finding, fp: str, fid: str, anchor: Anchor | None) -> None:
        marker = render_finding_marker(FindingMarker(v=1, id=fid, fp=fp, sev=f.severity, rule=f.rule))
        suggestion = redact(f.suggestion) if f.suggestion is not None and ctx.config.suggestions else None
        body = f"{safe_text(f.text)}\n\n{marker}"
        final = f.model_copy(update={"id": fid, "text": body, "suggestion": suggestion})
        if anchor is not None:
            ctx.vcs.post_inline(final, anchor)
        else:
            ctx.vcs.post_general(f"`{safe_text(f.path)}:{f.line}`\n\n{body}")
        ctx.posted.append(final)
