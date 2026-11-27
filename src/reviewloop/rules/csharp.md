---
stack: csharp
applies_to: ["**/*.cs", "**/*.cshtml", "**/*.razor"]
detect: [".csproj:<Project", ".sln:Microsoft Visual Studio Solution File", "global.json:sdk"]
---

# C# rules

## Null and bounds

- CS-1 [high] `!` or a dereference on a possibly null result (`FirstOrDefault`, `Find`, deserialized JSON). It throws `NullReferenceException`. Check first.
- CS-2 [medium] `dict[key]` without `TryGetValue`, or `list[0]` without a count check. It throws on missing or empty input. Use `TryGetValue` or check first.
- CS-3 [medium] `Single()` where duplicates are possible, or `First()` where none is possible. It throws on production data. Choose the operator that matches the invariant.
- CS-4 [medium] `double` or `float` for money, or `DateTime.Now` stored across servers. Rounding and time zone bugs appear. Use `decimal` and `DateTimeOffset.UtcNow`.

## Async

- CS-ASYNC-1 [high] `.Result`, `.Wait()` or `GetAwaiter().GetResult()` in request code. It blocks threads and can deadlock. Use `await` throughout.
- CS-ASYNC-2 [high] `async void` outside event handlers. Exceptions crash the process. Return `Task`.
- CS-ASYNC-3 [medium] Task not awaited in a request. Errors are lost and scoped services are disposed mid-work. Await it or use a background queue with its own scope.
- CS-ASYNC-4 [medium] `CancellationToken` accepted but not passed to I/O calls. Cancelled requests keep working. Pass it through.

## Resources and concurrency

- CS-RES-1 [medium] `IDisposable` stream or connection without `using`. It leaks handles. Add `using`.
- CS-RES-2 [medium] `new HttpClient()` per request. It exhausts sockets. Use `IHttpClientFactory`.
- CS-RES-3 [high] `DbContext` or another scoped service captured by a singleton or shared across threads. It causes concurrency exceptions. Create a scope with `IServiceScopeFactory`.
- CS-RES-4 [high] Shared `Dictionary` or `List` mutated from several threads, or `lock` on `this`. It races or deadlocks. Use `ConcurrentDictionary` or a private lock object.

## EF Core

- CS-EF-1 [medium] Navigation property accessed in a loop. It causes N+1 queries. Use `Include` or a `Select` projection.
- CS-EF-2 [medium] `ToList()` before `Where`, `Count` or paging. The whole table loads into memory. Stay on `IQueryable` until the end.
- CS-EF-3 [high] Dependent `SaveChanges` calls without a transaction. Partial writes persist. Use one `SaveChanges` or `BeginTransaction`.
- CS-EF-4 [high] Concurrent edits without a concurrency token. The last write silently wins. Add a row version and handle `DbUpdateConcurrencyException`.
- CS-EF-5 [critical] `FromSqlRaw` or `ExecuteSqlRaw` with concatenated input. It allows SQL injection. Use `FromSql` interpolation or explicit parameters.
- CS-EF-6 [critical] Migration drops or renames a column with data. Data is lost. Add, copy, then drop in a later release.

## ASP.NET Core

- CS-API-1 [critical] Endpoint lacks the `[Authorize]` policy siblings have, or gains `[AllowAnonymous]` on a state change. It allows unauthorized access. Restore it.
- CS-API-2 [high] Entity loaded by route ID without an owner or tenant check. It allows IDOR. Filter by the current user or use resource-based authorization.
- CS-API-3 [high] Request bound directly to an EF entity. Clients set protected properties. Bind to a DTO.
- CS-API-4 [high] Response field renamed or removed, or serializer settings changed. Clients break. Keep or version it.
- CS-API-5 [medium] Exception details returned to clients. Internals leak. Return generic `ProblemDetails`.

## Security

- CS-SEC-1 [critical] `BinaryFormatter`, or `TypeNameHandling` other than `None` on untrusted JSON. It allows remote code execution. Use `System.Text.Json` with concrete types.
- CS-SEC-2 [critical] `Process.Start` with a command built from input. It allows command injection. Use `ArgumentList` without a shell.
- CS-SEC-3 [high] `Html.Raw` or `MarkupString` on user data. It allows XSS. Encode it.
- CS-SEC-4 [high] `Path.Combine` with input and no prefix check on `Path.GetFullPath`. It allows traversal. Check the result stays under the base.
