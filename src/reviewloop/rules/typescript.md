---
stack: typescript
applies_to: ["**/*.ts", "**/*.tsx", "**/*.mts", "**/*.cts", "**/*.js", "**/*.jsx", "**/*.mjs", "**/*.cjs"]
detect: ["package.json:", "package.json:typescript", "tsconfig.json:compilerOptions"]
---

# TypeScript and JavaScript rules

## Types and null safety

- TS-1 [high] Non-null assertion (`!`) or `as` cast on a value that can be undefined, such as `find()`, `Map.get()` or an optional field. The compiler is silenced and it crashes at runtime. Narrow with a check and handle the missing case.
- TS-2 [medium] `any`, or an unchecked `as T` on parsed JSON or an external response. Wrong shapes flow through typed code. Validate with a schema (for example zod) at the boundary and type from it.
- TS-3 [medium] `||` used for a default where `0`, `""` or `false` are valid values. Valid input is replaced by the default. Use `??`.
- TS-4 [low] Array index or `arr[arr.length - 1]` read without a length check while `noUncheckedIndexedAccess` is off. The result is `undefined` typed as `T`. Check length or use `at()` with a guard.
- TS-5 [medium] `==` comparison or truthiness check across types. Coercion gives surprising matches. Use `===` and explicit checks.

## Async and errors

- TS-ASYNC-1 [high] Promise not awaited or returned (floating promise). Errors become unhandled rejections and work outlives the request. Await it, return it, or attach `.catch` with handling.
- TS-ASYNC-2 [medium] `await` inside a `for` loop over independent items. Calls run serially and latency adds up. Use `Promise.all` with a concurrency bound.
- TS-ASYNC-3 [medium] `forEach` with an async callback. The loop does not wait and errors are lost. Use `for...of` with await or `Promise.all(map(...))`.
- TS-ASYNC-4 [medium] `Promise.all` used where partial failure must be handled. One rejection discards all results and leaves other calls running. Use `Promise.allSettled` and handle each result.
- TS-ERR-1 [medium] `catch (e)` that reads `e.message` without narrowing, or that swallows the error. Non-Error throws crash the handler; failures go silent. Narrow with `instanceof Error` and rethrow or handle.
- TS-ERR-2 [medium] Timer, listener, stream or subscription created without cleanup. Memory and handles leak in long-running processes. Clear or remove it on completion.

## Security

- TS-SEC-1 [critical] `eval`, `new Function`, or `child_process.exec` with interpolated input. It allows code or command injection. Avoid eval; use `execFile` or `spawn` with an argument array.
- TS-SEC-2 [high] Object merge or property set with keys from input (`obj[key] = value`, deep merge). It allows prototype pollution via `__proto__`. Reject those keys or use `Map` or `Object.create(null)`.
- TS-SEC-3 [critical] SQL or query built with template literals. It allows injection. Use parameterized queries or the query builder's bindings.
- TS-SEC-4 [high] Path from input passed to `fs` or `path.join` without checking the resolved path stays under a base directory. It allows path traversal. Resolve and check the prefix.
- TS-SEC-5 [high] `Math.random()` used for tokens, IDs or secrets. It is predictable. Use `crypto.randomUUID()` or `crypto.randomBytes`.

## API and modules

- TS-API-2 [medium] Mutating a shared module-level object or a function argument the caller still uses. Callers see unexpected changes. Copy before modifying.
- TS-API-3 [medium] Date parsed with `new Date(string)` from non-ISO input. Results vary by runtime and time zone. Parse with an explicit format.
