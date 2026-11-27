---
name: security-review
description: OWASP-oriented security review of a pull request diff that reports only vulnerabilities with a concrete path from input to impact.
---

# Security review

Follow the output-contract skill for every output rule.

## Inputs

- `.reviewloop/context.md`, `.reviewloop/diff/` index and files, `.reviewloop/rules/*.md`, `.reviewloop/previous-findings.json` (do not repeat).
- `.reviewloop/security/scan.json` when present: scanner results (for example opengrep, gitleaks). These are hints to verify, not findings to copy. Report a scanner hit only after you confirm it in the code, and write your own text. Drop hits in tests, fixtures and examples unless that code ships.

## Method

1. Find the entry points the diff adds or changes: HTTP handlers, message consumers, CLI arguments, file and network readers, webhooks, scheduled jobs.
2. For each, trace untrusted data (request fields, headers, cookies, uploaded files, URLs, queue messages, data from other services) to the sinks below.
3. For data-flow classes (injection, XSS, SSRF, deserialization, path traversal, IDOR), report only when you can name the source, the path and the sink, and no validation or encoding on that path stops it.
4. Condition classes need no data flow: committed secrets, crypto misuse, TLS verification disabled, dependency risk and security misconfiguration (CORS, CSRF, debug on). Report them when the quoted code shows the condition, except placeholders, test fixtures and code that does not ship.

## Checklist

- Injection: SQL or NoSQL built by string concatenation or interpolation; shell commands with user input; LDAP, XPath, template and expression-language injection; header and log injection (CRLF). Fix: parameterized queries, argument arrays without a shell, allowlists.
- Cross-site scripting: user data rendered without context encoding; raw HTML sinks; unsafe URL schemes (`javascript:`) in links.
- Authentication: endpoints missing the auth guard used by their siblings; tokens or sessions not validated (signature, expiry, audience); passwords compared or stored without a slow salted hash; login without rate limiting.
- Authorization: object looked up by an ID from the request without an ownership or tenant check (IDOR); role checks only in the client; mass assignment that lets a user set role, owner or price fields.
- Secrets: credentials, API keys, private keys or tokens committed in code or config; secrets logged, returned in responses or error messages. Never quote the value; name the file and variable.
- SSRF: server-side requests to a URL or host taken from input without an allowlist; redirects followed to internal addresses; cloud metadata endpoints reachable.
- Deserialization: native object deserialization of untrusted data (Java serialization, Python pickle, PHP `unserialize`, .NET BinaryFormatter, YAML loaders that construct objects).
- Path traversal: file paths built from input without normalizing and checking they stay inside a base directory; archive extraction without entry path checks (zip slip).
- Crypto misuse: MD5 or SHA-1 for passwords or signatures; ECB mode; static or reused IVs and nonces; hard-coded keys; non-cryptographic random for tokens; TLS verification disabled; comparing secrets with a non-constant-time compare.
- Dependency risk: a new or upgraded dependency in a manifest that is unmaintained, typosquatted, pulled from an unpinned URL or git branch, or has a known vulnerability affecting the used feature.
- Other: open redirects; CSRF protection disabled on cookie-authenticated state changes; CORS reflecting any origin with credentials; XML parsers with external entities enabled; regex on input with catastrophic backtracking; files uploaded without type and size limits served from the app origin.

## Findings

- Set `security: true` on every finding. Never include `suggestion`.
- Category `security`, or `authorization` for access control.
- Severity: `critical` for remote exploitation without authentication, or secret exposure; `high` for exploitation needing a normal account or a likely precondition; `medium` for hardening with a plausible path; do not report purely theoretical issues.
- `text` names the source, the path, the sink, the impact, and the fix.
- Add one `coverage` entry per input file, as the output contract requires.
