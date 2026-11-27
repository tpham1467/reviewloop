---
stack: go
applies_to: ["**/*.go"]
detect: ["go.mod:module"]
---

# Go rules

## Nil and bounds

- GO-1 [high] Pointer, interface or map lookup used without a nil or `ok` check. It panics or uses a zero value. Check `v, ok := m[k]` and nil first.
- GO-2 [high] Write to a nil map. It panics. Initialize with `make` or a literal.
- GO-3 [medium] Slice index or `s[a:b]` with input-derived bounds and no length check. It panics. Check length first.
- GO-4 [medium] Function returns a typed nil pointer as an `error` or other interface. `err != nil` is true. Return a literal `nil`.

## Errors

- GO-ERR-1 [high] Returned `error` ignored or assigned to `_`. Execution continues with zero values. Check it.
- GO-ERR-2 [medium] Error wrapped with `%v`, or compared with `==`. `errors.Is` and `errors.As` fail on wrapped errors. Wrap with `%w`; compare with `errors.Is`.
- GO-ERR-3 [medium] `panic` for expected failures in handlers or libraries. It crashes the process. Return an error.

## Resources

- GO-RES-1 [medium] `resp.Body`, file or `rows` not closed, or `defer Close()` before the error check. It leaks connections or panics on nil. Check the error, then defer `Close()`.
- GO-RES-2 [medium] `defer` inside a loop. Resources stay open until the function returns. Close per iteration or extract a function.
- GO-RES-3 [medium] `http.Client` or `http.Get` without a timeout. Requests hang forever. Set `Timeout` or a context deadline.
- GO-RES-4 [medium] `rows.Err()` not checked after iteration. Partial results look complete. Check it after the loop.

## Concurrency

- GO-CON-1 [high] Map or field written from several goroutines without a mutex or channel. It races and can crash on concurrent map writes. Guard it with `sync.Mutex`.
- GO-CON-2 [high] Goroutine with no way to stop. It leaks per request. Pass a `context.Context` and return on `Done()`.
- GO-CON-3 [medium] Loop variable captured by a goroutine while `go.mod` targets Go below 1.22. All goroutines see the last value. Pass it as an argument.
- GO-CON-4 [medium] `WaitGroup.Add` called inside the goroutine. `Wait` returns early. Call `Add` before `go`.
- GO-CON-5 [medium] Send with no guaranteed receiver, or unbounded goroutines per item. It deadlocks or exhausts memory. Use `select` with context or a worker pool.
- GO-CON-6 [medium] Struct with a `sync.Mutex` copied by a value receiver. The lock stops protecting state. Use pointer receivers.
- GO-CON-7 [medium] `context.Background()` in a request path. Cancellation and deadlines are lost. Pass the request context.

## Data access

- GO-DB-1 [high] Several writes without `BeginTx`, or no `defer tx.Rollback()`. Partial writes persist or transactions leak. Begin one and defer rollback.

## Security

- GO-SEC-1 [critical] SQL built with `fmt.Sprintf` or concatenation. It allows injection. Use placeholders.
- GO-SEC-2 [critical] `exec.Command("sh", "-c", input)`. It allows command injection. Pass arguments without a shell.
- GO-SEC-3 [high] `filepath.Join` with input and no base-prefix check. It allows traversal. Check the cleaned path or use `os.Root`.
- GO-SEC-4 [high] `text/template` for HTML, or `template.HTML` from user data. It allows XSS. Use `html/template`.
- GO-SEC-5 [high] `InsecureSkipVerify: true`, or `math/rand` for tokens. It allows interception or guessing. Verify TLS; use `crypto/rand`.
- GO-SEC-6 [critical] Handler without the auth middleware its siblings use, or without an ownership check. It allows unauthorized access. Add both.

## API

