---
stack: nestjs
applies_to: ["**/*.ts"]
detect: ["package.json:@nestjs/core"]
---

# NestJS rules

## Validation and contracts

- NEST-1 [high] DTO parameter with no `ValidationPipe` applied, or a DTO without class-validator decorators. Input is not validated at all. Enable a global `ValidationPipe` and decorate every field.
- NEST-2 [high] `ValidationPipe` without `whitelist: true` while the DTO is spread into an entity. Clients set fields like `role` or `ownerId`. Enable whitelisting and map fields explicitly.
- NEST-3 [medium] Numeric or boolean param used without `ParseIntPipe`, `ParseUUIDPipe` or `transform: true`. The value is a string and comparisons fail. Use the parse pipes.
- NEST-4 [medium] Nested DTO without `@ValidateNested()` and `@Type(() => Child)`. Nested fields are not validated. Add both.
- NEST-API-1 [high] Route path, method, status or response shape changed on an existing endpoint. Clients break. Version the route or keep the shape.
- NEST-API-2 [high] Entity returned from a controller. Password hashes and internal fields leak. Return a response DTO, or use `ClassSerializerInterceptor` with `@Exclude`.

## Auth

- NEST-AUTH-1 [critical] Route without the guard its siblings use, or `@Public()` added to a route that changes data. Anonymous users reach it. Apply the guard.
- NEST-AUTH-2 [high] Resource loaded by request ID without an owner or tenant check. It allows IDOR. Filter by owner in the query or check in a guard.
- NEST-AUTH-3 [high] Custom guard returns true on missing metadata or on caught errors. Failures grant access. Default to deny.

## Errors

- NEST-ERR-1 [medium] Service throws generic `Error` or returns `null` for not-found or bad input. Clients get 500. Throw `NotFoundException` or `BadRequestException`.
- NEST-ERR-2 [medium] Exception filter returns raw error messages or stacks. Internals leak. Log details; return a generic message.
- NEST-ERR-3 [high] Async work in a controller or handler not awaited. Errors are unhandled and the request reports success. Await it or use a queue.

## Data access

- NEST-DB-1 [high] Several writes in one method without a transaction (TypeORM `transaction`, Prisma `$transaction`). A partial failure corrupts data. Wrap them and use the transactional client for every write.
- NEST-DB-2 [high] Inside a transaction callback, a write uses the injected repository instead of the callback's manager. That write escapes the transaction. Use the manager.
- NEST-DB-3 [medium] Relation loaded per item in a loop. It causes N+1. Use `relations`, a join, or `include`.
- NEST-DB-4 [critical] `query()` or query builder `where()` with interpolated strings. It allows SQL injection. Use bound parameters.
- NEST-DB-5 [medium] `find` without `take` on a growing table. Responses are unbounded. Paginate.

## Framework pitfalls

- NEST-5 [medium] `Scope.REQUEST` added to a widely injected provider. Every dependent becomes request-scoped and slows down. Pass request data explicitly or use `AsyncLocalStorage`.
- NEST-6 [medium] Per-request data stored on a singleton provider. Concurrent requests overwrite each other. Keep it in method scope.
- NEST-7 [medium] New circular dependency hidden with `forwardRef`. Injection order bugs appear at startup. Extract shared logic into a third provider.
- NEST-8 [high] Required env read via `ConfigService.get` without startup validation. The app fails on first use, not at boot. Validate in `ConfigModule.forRoot`.
