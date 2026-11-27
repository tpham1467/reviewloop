---
name: code-review
description: Method for reviewing a pull request diff and reporting concrete, anchored findings.
---

# Code review

Follow the output-contract skill for every output rule.

## Inputs

1. `.reviewloop/context.md`: PR title, branches, business context, and settings (language, suggestions, security).
2. `.reviewloop/diff/` index: the list of input files and their status. Then each file's diff.
3. `.reviewloop/rules/*.md`: the rules selected for this PR. Cite a rule ID in `rule` when a finding applies one.
4. `.reviewloop/previous-findings.json`: findings already posted. Do not report them again, even reworded.

Read the full current file, not only the hunk, when the hunk alone cannot show whether code is correct. Use `git log`, `git blame`, `git show` or `git diff` when history explains intent.

## Method

For each changed file:

1. Work out what the change is meant to do, from the business context and the code.
2. Trace each changed function: inputs, branches, error paths, return values, side effects.
3. Check the callers and callees of changed signatures, return types, defaults, thrown errors and data shapes. Search the repository for usages.
4. Check against the rules files and these questions:
   - Does it do what the context asks? Is any acceptance criterion contradicted?
   - Null, empty, zero, negative, very large, duplicate, concurrent input.
   - Errors swallowed, wrong status codes, partial writes without a transaction.
   - Resources opened and not closed; locks not released.
   - Shared state mutated without synchronization; check-then-act races.
   - Queries inside loops, missing pagination, unbounded reads.
   - Authorization on every new entry point and every object looked up by an ID from the request.
   - Behavior change for existing callers, stored data, public API, config, migrations.
   - Secrets or personal data written to logs.
5. Confirm each suspicion in the code before reporting it. If you cannot point at the line that fails, do not report it.

## Scope

- Report problems in changed code.
- Report unchanged code only when the change breaks it. Then quote the broken (unchanged) code as `existing_code`, quote the changed line that causes it as `cause_code`, and name the broken location in `text`. Report each broken place as its own finding.
- Ignore problems that existed before the change and are untouched by it.

## Writing findings

- One finding per distinct problem. The same mistake in several places is one finding per place only when each place needs its own fix.
- Be concrete: name the function or variable, the input or condition that triggers the problem, the effect, and the fix.
- No praise, no summaries of what the code does, no questions without a claim.
- No style nits a formatter or linter fixes (spacing, import order, quotes, naming taste).
- No speculative "consider" findings without a concrete failure.
- Severity follows the output contract. Do not inflate.
- Add a `suggestion` only when the corrected lines are certain and suggestions are enabled.

## Coverage

Add one `coverage` entry per file in the diff index. `reviewed` when you read its diff. `excluded` with the reason when the index marks it excluded. `failed` with the reason when you could not read or understand it.

## Security (conditional)

Applies only when the prompt says security is enabled. Otherwise skip this section; a separate security step handles it.

- Also check changed code for injection, missing authentication or authorization, secrets in code, SSRF, unsafe deserialization, path traversal and crypto misuse, as listed in the security-review skill.
- Report these with category `security` or `authorization`, no `suggestion`, and only with a concrete path from attacker-controlled input to the dangerous call.
