---
name: pr-overview
description: Write a short markdown overview of what a pull request changes and its main risks for the summary comment.
---

# PR overview

Follow the output-contract skill for every output rule.

## Input

`.reviewloop/context.md` (title, business context) and the diff under `.reviewloop/diff/`.

## Method

1. Read the diff index and enough of each diff to know what changed in behavior, not just which files moved.
2. Group the change by effect: new feature, behavior change, refactor, schema or migration, config, dependencies, tests.
3. Identify the main risks from the code: changed public API or data shape, migrations on existing data, concurrency, authorization paths, removed checks, missing tests for new logic.

## Output

`{"markdown": str}` in the configured language, at most about 120 words:

- One sentence on the purpose of the PR.
- Two to five bullets on what changes, naming modules or endpoints.
- A line starting with "Risks:" naming at most three concrete risks, or "Risks: none identified."

Rules:

- No findings list, no severities, no scores, no file coverage; Python renders those.
- No praise, no restating the title, no headings.
- State facts from the code. Do not repeat the author's claims (for example "fully tested") unless the diff shows them.
