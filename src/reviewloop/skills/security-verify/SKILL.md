---
name: security-verify
description: Independent second pass over security findings that keeps only those with a concrete exploitable path shown in the code.
---

# Security verify

Follow the output-contract skill for every output rule.

## Input

A numbered list of security findings, each with an `id` (integer), `path`, `line`, `severity`, `text` and quoted code, plus the diff under `.reviewloop/diff/` and the repository.

## Stance

You did not write these findings. Verify each one from the code as if seeing it for the first time. Do not trust the finding's reasoning; rebuild the path yourself.

## Per finding

1. Source: identify where attacker-controlled data enters. Is it really reachable by an attacker (public route, authenticated user, message from another tenant), or only by trusted code, config or an administrator?
2. Path: follow the data from the source to the quoted code. Note every validation, type conversion, encoding, allowlist, ORM parameterization or framework default on the way.
3. Sink: confirm the quoted code does the dangerous thing claimed (executes a query, a command, a file access, a request, a deserialization).
4. Guards elsewhere: check middleware, guards, decorators, policies, base classes, routing config and framework defaults that apply to this entry point.
5. Condition classes (committed secrets, crypto misuse, TLS verification disabled, dependency risk, security misconfiguration such as CORS, CSRF or debug on) have no source-path-sink. Keep them when the quoted code shows the condition, unless it is a placeholder, a test fixture value, a public identifier or code that does not ship.

For data-flow classes (injection, XSS, SSRF, deserialization, path traversal, IDOR), keep the finding only when all of steps 1 to 3 hold and step 4 finds no guard that blocks it. Missing a link means drop. The condition-class rule in step 5 is the exception to this keep rule.

Do not add new findings. Do not change severity or text.

## Output

`{"analysis": str, "kept_ids": [int]}`. Write `analysis` first: for each id, the source, path and sink you confirmed, or the specific link that is missing or the guard that blocks it. Then list the ids you keep.
