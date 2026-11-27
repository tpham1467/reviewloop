---
stack: php
applies_to: ["**/*.php"]
detect: ["composer.json:\"php\"", "composer.json:\"require\""]
---

# PHP rules

## Types and null safety

- PHP-1 [high] Offset, property or method used on a value that can be `null` or `false` (`array_search`, `strpos`, `json_decode`, a DB fetch). It warns, misbehaves or throws. Check with `=== null` or `=== false`.
- PHP-2 [high] Loose `==`, or `in_array`/`array_search` without strict mode on mixed types. `"abc" == 0` and `"1e3" == "1000"` match wrongly. Use `===` and the strict flag.
- PHP-3 [medium] `if ($value)` or `empty()` where `"0"`, `0` or `[]` are valid. Valid input looks missing. Use `isset` or explicit comparison.
- PHP-5 [medium] `json_decode` without `JSON_THROW_ON_ERROR`. Invalid JSON becomes `null`. Pass the flag.
- PHP-6 [medium] Float used for money. Cents drift. Use integer minor units or a decimal library.

## Errors and resources

- PHP-ERR-1 [medium] Error suppressed with `@`, or an empty `catch (\Throwable)`. Failures disappear. Handle or rethrow with context.
- PHP-ERR-2 [medium] File, curl handle or lock not closed in `finally`. It leaks in long-running workers. Close in `finally`.
- PHP-ERR-3 [medium] HTTP call without connect and total timeouts. Workers hang. Set both.
- PHP-ERR-4 [medium] Shared `DateTime` mutated, or created without a zone. Callers see changed or shifted dates. Use `DateTimeImmutable` with a zone.

## Data access

- PHP-DB-1 [critical] SQL built by interpolating variables into `query()` or `mysqli_query`. It allows SQL injection. Use prepared statements with bound parameters.
- PHP-DB-2 [high] Several writes without `beginTransaction`, `commit`, and `rollBack` on failure. Partial writes persist. Wrap them.
- PHP-DB-4 [medium] PDO without `ERRMODE_EXCEPTION`. Failed queries return false unnoticed. Set the error mode.

## Security

- PHP-SEC-1 [critical] `unserialize` on user-controlled data. It allows object injection and code execution. Use `json_decode`, or `allowed_classes => false`.
- PHP-SEC-2 [critical] `eval`, string `assert`, or `include` with a path from input. It allows code execution. Map input to an allowlist.
- PHP-SEC-3 [critical] `exec`, `shell_exec`, `system` or backticks with input not passed through `escapeshellarg`. It allows command injection. Escape each argument or avoid the shell.
- PHP-SEC-4 [high] User data echoed without `htmlspecialchars($v, ENT_QUOTES, 'UTF-8')`. It allows XSS. Escape on output.
- PHP-SEC-5 [high] Path from input passed to `fopen`, `file_get_contents` or `unlink` without `realpath` and a base check. It allows traversal. Check the prefix.
- PHP-SEC-6 [high] `file_get_contents` or curl on a URL from input. It allows SSRF. Allowlist hosts.
- PHP-SEC-7 [high] `md5` or `sha1` for passwords; `rand` or `mt_rand` for tokens. They are crackable or guessable. Use `password_hash` and `random_bytes`.
- PHP-SEC-8 [high] Secret compared with `==` or `===`. It leaks timing. Use `hash_equals`.
- PHP-SEC-9 [high] Upload stored under the web root with its client name. It allows script upload. Store outside it with a generated name.

## API

