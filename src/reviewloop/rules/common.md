---
stack: common
applies_to: ["**/*"]
detect: []
---

# Common rules

## Correctness

- CMN-1 [high] Value that can be null or empty is dereferenced or indexed unchecked. It crashes on such input. Check it first.
- CMN-2 [medium] Off-by-one in a loop bound, slice, page offset or range comparison. Records are skipped or read past the end. Use half-open ranges consistently.
- CMN-3 [high] A changed signature, return shape, default or thrown error is not updated at every caller. Unchanged callers break at runtime. Update all callers in the same change.
- CMN-4 [medium] Float used for money, integer overflow, or division by a value that can be zero. Results are silently wrong. Use integer minor units or decimals and guard the divisor.
- CMN-5 [medium] Times compared or stored in local time. Logic breaks across time zones and daylight saving. Store and compare in UTC.

## Error handling

- CMN-ERR-1 [medium] Error caught and ignored, or replaced by a default value. Failures become silent wrong results. Handle it, or rethrow with context.
- CMN-ERR-2 [medium] Retry without a bound, backoff or idempotency. It amplifies outages and repeats side effects. Bound retries and retry only idempotent operations.
- CMN-ERR-3 [medium] External call without a timeout. One slow dependency exhausts workers. Set connect and read timeouts.

## Resources and concurrency

- CMN-6 [medium] File, socket, connection or lock not released on every path, including errors. It leaks until the process fails. Use the language's scoped release construct.
- CMN-7 [high] Check-then-act on shared state without a lock, atomic operation or unique constraint. Concurrent requests both pass the check. Use an atomic update or a constraint.
- CMN-8 [high] Related writes done without a transaction. A failure midway leaves half-updated data. Wrap them in one transaction; keep external calls outside it.
- CMN-9 [medium] In-memory state used for correctness across requests (locks, one-time tokens). It is lost on restart and wrong with several instances. Keep it in the database.

## Data and API

- CMN-API-1 [high] Public API, event payload, CLI flag or config key renamed, removed or changed in meaning. Existing clients break. Keep the old form or version the contract.
- CMN-API-2 [critical] Migration drops or rewrites a column that still holds data, or cannot be reversed. Data is lost. Add, backfill, switch readers, then remove in a later release.
- CMN-API-3 [medium] List endpoint or query without a limit. Memory and latency grow with data. Paginate and cap the page size.

## Performance

- CMN-PERF-1 [medium] Database or HTTP call inside a loop (N+1). Latency grows with list size. Batch it or use a join.
- CMN-PERF-2 [low] Same expensive call repeated in one flow. It wastes time. Compute once and reuse.

## Security

- CMN-SEC-1 [critical] Credential, token or private key committed in code or config. Anyone with repository access can use it. Load it from the environment or a secret store and rotate it.
- CMN-SEC-2 [high] Secret, token or personal data written to logs, errors or responses. Logs are widely readable. Log identifiers only.
- CMN-SEC-3 [critical] Query, shell command or file path built by concatenating input. It allows injection or traversal. Use parameters, argument arrays and a base-directory check.
- CMN-SEC-4 [critical] New entry point or lookup by request ID without an authorization check. Users reach other users' data. Apply the sibling guard and check ownership.
- CMN-SEC-5 [medium] External input used without validating type, length and range. A crash, unbounded allocation or a value reaching a query or command follows. Validate once at the boundary.
