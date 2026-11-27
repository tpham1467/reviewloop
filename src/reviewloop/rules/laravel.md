---
stack: laravel
applies_to: ["**/*.php"]
detect: ["composer.json:laravel/framework"]
---

# Laravel rules

## Validation and input

- LAR-1 [high] Controller uses `$request->input()` or `all()` without a `FormRequest` or `validate()`. Unvalidated input reaches queries. Validate and use `$request->validated()`.
- LAR-2 [high] `create($request->all())`, `fill($request->all())` or `$guarded = []`. Users set fields such as `is_admin` or `user_id`. Pass validated data and define `$fillable`.
- LAR-3 [medium] Rules lack `exists:`, size limits or nested array rules. Bad IDs and huge payloads pass. Add them.

## Authorization

- LAR-AUTH-1 [critical] New route outside the `auth` middleware group its siblings use. Anonymous users reach it. Add the middleware.
- LAR-AUTH-2 [high] Model loaded by route binding or ID without a policy check (`authorize`, `can` middleware, `Gate`). Users act on others' records. Authorize against the model.
- LAR-AUTH-3 [high] `FormRequest::authorize()` returns `true` for a request changing owned data. Authorization is skipped. Check the policy.
- LAR-AUTH-4 [high] Query not scoped to the user or tenant. It leaks data across accounts. Query through the relation (`$user->orders()`) or a global scope.

## Queries

- LAR-DB-1 [medium] Relation accessed in a loop, Blade view or resource without `with()` or `load()`. It causes N+1. Eager load; enable `preventLazyLoading()` outside production.
- LAR-DB-2 [medium] `get()` or `all()` on a growing table, then filtering in PHP. Memory grows with data. Filter in the query; use `paginate`, `chunkById` or `count()`.
- LAR-DB-3 [critical] `whereRaw`, `orderByRaw`, `DB::raw` or `DB::statement` with interpolated input. It allows SQL injection. Pass bindings; allowlist sort columns.
- LAR-DB-4 [high] `first()` used without a null check. It throws on a missing record. Use `firstOrFail()` or `findOrFail()`.
- LAR-DB-5 [high] Read-modify-write of counters or balances. Concurrent requests lose updates. Use `increment()` or `lockForUpdate()` inside a transaction.

## Transactions and jobs

- LAR-TX-1 [high] Several writes in one action without `DB::transaction()`. A failure leaves partial data. Wrap them.
- LAR-TX-2 [high] Job, mail or event dispatched inside a transaction and reading the new rows. The worker runs before commit. Use `afterCommit()`.
- LAR-JOB-1 [medium] Non-idempotent job with `$tries` above one. Retries repeat charges or emails. Make it idempotent.
- LAR-JOB-2 [medium] Large payload serialized into a delayed job. Data is stale or deleted when it runs. Pass IDs and handle a missing model.

## Migrations

- LAR-MIG-1 [critical] Migration drops or renames a column deployed code still uses. Deploys fail and data is lost. Add first; remove in a later release.
- LAR-MIG-2 [medium] Foreign key column without index or constraint. Joins slow down and orphans appear. Use `foreignId()->constrained()`.

## API and output

- LAR-API-1 [high] Model returned directly from a controller, or `$hidden` changed. Password hashes and internal fields leak, and schema changes become API changes. Return an API Resource with explicit fields.
- LAR-API-2 [high] Resource field renamed, removed or retyped on an existing endpoint. Clients break. Version or keep the field.

## Security

- LAR-SEC-1 [critical] Blade `{!! $value !!}` with user data. It allows XSS. Use `{{ }}` or sanitize.
- LAR-SEC-2 [high] Route excluded from CSRF verification under session auth. It allows CSRF. Remove the exclusion.
- LAR-SEC-3 [high] `env()` outside config files. It returns null once config is cached. Read through `config()`.
- LAR-SEC-5 [high] Upload stored on the public disk with its client name, or without `mimes` and `max` rules. Users upload scripts. Validate and `store()` with generated names.
