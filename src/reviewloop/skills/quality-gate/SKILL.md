---
name: quality-gate
description: Filter candidate review findings on two narrow grounds and rewrite vague ones so each names the code and the fix.
---

# Quality gate

Follow the output-contract skill for every output rule.

## Input

- A numbered list of candidate findings. Each has an `id` (integer), `path`, `line`, `severity`, `category`, `text`, `existing_code` and possibly `cause_code`.
- The diff under `.reviewloop/diff/`.

## Output

`{"analysis": str, "drop_ids": [int], "rewrites": {"<id>": str}}`, written in that order. Write `analysis` first: for each finding you drop or rewrite, one or two sentences with the id, the ground, and the diff line that proves it. Then derive `drop_ids` and `rewrites` from the analysis. Findings not mentioned are kept unchanged.

## Default is keep

You are not re-reviewing. You remove only findings that are provably wrong about the diff. When in doubt, keep.

## Drop grounds

Drop a finding only on one of these two grounds:

- Ground A: neither its `existing_code` nor its `cause_code` appears in the diff (after ignoring whitespace differences). The finding is about code this PR does not contain or touch.
- Ground B: a line in the diff directly contradicts the claim, with no multi-step reasoning. Example: the finding says a value is never checked for null and the diff shows `if (user == null) return;` immediately before the use.

Not grounds for dropping:

- Low value, low severity, or "minor".
- You would have worded it differently.
- You think it is probably fine but cannot point at a contradicting diff line.
- It needs several steps of reasoning to confirm or refute.
- It is a duplicate of a previously posted finding (Python handles duplicates).

## Protected categories

Never drop a finding in these categories, even on ground A or B; at most rewrite it:

- behavior or API contract change
- transactions and concurrency
- authorization
- data loss
- null or bounds safety

Judge membership by what the finding is about, not only by its `category` field.

## Rewrites

A vague finding is rewritten, not dropped. Rewrite when the text does not name the code concerned, the triggering condition, or the fix. The rewrite:

- names the function, variable or line concerned;
- states the concrete failure and when it happens;
- states the fix;
- keeps the original claim, severity and meaning; adds no new claims;
- is in the configured language, two to five sentences.

Do not rewrite findings that are already concrete.
