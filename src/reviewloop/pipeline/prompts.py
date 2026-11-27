"""Short prompt templates: which skill to follow, which `.reviewloop/` inputs to read, where to write.

The review method lives in the skills; prompts only wire inputs and outputs.
"""

_FOOTER = (
    "Write all human-readable text in language `{language}`. Use `git diff -- <path>`, `git log -- <path>`, "
    "`git blame -- <path>` or `git show` when history helps; put paths after `--`. "
    "Write the result as JSON to `.reviewloop/out/{step}.json` and change no other file."
)


def _prompt(step: str, language: str, body: str) -> str:
    return f"{body}\n\n{_FOOTER.format(language=language, step=step)}"


def summarize_context(step: str, language: str) -> str:
    return _prompt(
        step,
        language,
        "Follow the summarize-context skill. Inputs: the tickets in `.reviewloop/tickets/` and the PR title and "
        "body in `.reviewloop/context.md`.",
    )


_SECURITY = {
    True: "A separate security step runs: Skip the Security section of the code-review skill.",
    False: "Security is enabled for this step: apply the Security section of the code-review skill.",
}


def review(step: str, language: str, suggestions: bool, security_step: bool) -> str:
    return _prompt(
        step,
        language,
        f"Follow the code-review skill. {_SECURITY[security_step]} Inputs: `.reviewloop/context.md`, "
        "`.reviewloop/diff/index.md` and the per-file diffs under `.reviewloop/diff/`, `.reviewloop/rules/*.md`, "
        f"`.reviewloop/previous-findings.json`. Suggestions are {'enabled' if suggestions else 'disabled'}.",
    )


def coverage_repair(base: str, missing_file: str) -> str:
    return (
        f"{base}\n\nYour previous output left out coverage for the files listed in `{missing_file}`. Write the "
        "output file again with `findings: []` and one coverage entry for every file in the diff index, as the "
        "output contract requires."
    )


def security_review(step: str, language: str) -> str:
    return _prompt(
        step,
        language,
        "Follow the security-review skill. Inputs: `.reviewloop/context.md`, `.reviewloop/diff/index.md` and the "
        "per-file diffs under `.reviewloop/diff/`, `.reviewloop/rules/*.md`, `.reviewloop/previous-findings.json`, "
        "and `.reviewloop/security/scan.json` (scanner hints). When a finding confirms a scanner hit, set its `rule` "
        "to `<tool>:<rule>` of that hit.",
    )


def security_verify(step: str, language: str) -> str:
    return _prompt(
        step,
        language,
        "Follow the security-verify skill. Input: the numbered findings in `.reviewloop/security/findings.json`, "
        "the diffs under `.reviewloop/diff/` and the repository.",
    )


def pr_overview(step: str, language: str) -> str:
    return _prompt(
        step,
        language,
        "Follow the pr-overview skill. Inputs: `.reviewloop/context.md`, `.reviewloop/diff/index.md` and the "
        "per-file diffs under `.reviewloop/diff/`.",
    )


def quality_gate(step: str, language: str) -> str:
    return _prompt(
        step,
        language,
        "Follow the quality-gate skill. Inputs: the numbered findings in `.reviewloop/gate/findings.json` and the "
        "diffs under `.reviewloop/diff/`.",
    )


def anchor_repair(step: str, language: str, repair_file: str) -> str:
    return _prompt(
        step,
        language,
        f"Each finding in `findings` of `{repair_file}` quotes code (`existing_code`, `cause_code`) that is not in "
        "the file it names. Read the files and write a review output whose `findings` has exactly one entry per "
        "input finding, in the same order: the same finding with corrected `path`, `line`, `side` and verbatim "
        'quotes, or with `existing_code` set to "" when you cannot find its code. `coverage` has one entry per file '
        f"in `files` of `{repair_file}`.",
    )


def follow_up(step: str, language: str) -> str:
    return _prompt(
        step,
        language,
        "Follow the follow-up skill. Inputs: one JSON file per open thread in `.reviewloop/follow-up/`, and "
        "`.reviewloop/diff/index.md` with the per-file diffs under `.reviewloop/diff/` (the changes since the "
        "previous review).",
    )
