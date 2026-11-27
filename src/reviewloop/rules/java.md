---
stack: java
applies_to: ["**/*.java"]
detect: ["pom.xml:<project", "build.gradle:java", "build.gradle:org.springframework", "build.gradle.kts:java", "build.gradle.kts:org.springframework"]
---

# Java rules

## Null and bounds

- JAVA-1 [high] Result of `Map.get`, a lookup or a nullable field dereferenced without a check. It throws `NullPointerException`. Check it, or return `Optional` and handle empty.
- JAVA-2 [medium] `Optional.get()` without a presence check. It throws `NoSuchElementException`. Use `orElseThrow` with a specific exception, or `map`.
- JAVA-3 [medium] `list.get(0)`, `substring` or array index without a size check. It throws on empty input. Check bounds.
- JAVA-4 [high] `Integer`, `Long` or `String` compared with `==`. It compares references and fails outside the cache range. Use `equals`.
- JAVA-5 [medium] `equals` without `hashCode`, or mutable fields in `hashCode` of set members. Lookups fail. Override both on immutable fields.

## Errors and resources

- JAVA-ERR-1 [medium] Empty catch, or catching `Exception` and returning null. Failures disappear. Handle, or rethrow with the cause.
- JAVA-ERR-2 [medium] Stream, connection or `ResultSet` not closed on every path. It leaks on exceptions. Use try-with-resources.
- JAVA-ERR-3 [medium] `InterruptedException` swallowed. Shutdown and cancellation break. Call `Thread.currentThread().interrupt()` and stop.
- JAVA-ERR-4 [medium] `BigDecimal` built from a `double`, or compared with `equals`. Values and comparisons are wrong. Use `BigDecimal.valueOf` and `compareTo`.

## Concurrency

- JAVA-CON-1 [high] `HashMap`, `ArrayList` or `SimpleDateFormat` shared across threads, or mutable fields on a singleton bean. State corrupts. Use concurrent collections, `java.time`, or local state.
- JAVA-CON-2 [high] `containsKey` then `put` on a shared map. Both threads insert. Use `computeIfAbsent`.
- JAVA-CON-3 [medium] Executor created per call and never shut down, or unbounded. Threads leak. Use a shared bounded executor.
- JAVA-CON-4 [high] Lock not released in `finally`. An exception holds it forever. Unlock in `finally`.

## Spring and JPA

- JAVA-JPA-1 [medium] Lazy association accessed in a loop. It causes N+1. Use `JOIN FETCH` or an entity graph.
- JAVA-JPA-2 [high] `@Transactional` on a private method or a self-call. The proxy is bypassed and no transaction runs. Make it public and call it through another bean.
- JAVA-JPA-3 [high] Multiple writes without `@Transactional`, or a checked exception that skips rollback. Partial writes persist. Annotate and set `rollbackFor`.
- JAVA-JPA-4 [medium] Remote call inside a transaction. It holds connections and locks. Move it outside.
- JAVA-JPA-5 [high] Concurrently edited entity without `@Version`. The last write silently wins. Add optimistic locking.
- JAVA-API-1 [high] Entity returned from a controller or bound from a request body. Internal fields leak and clients set protected fields. Use DTOs.
- JAVA-API-2 [high] Request DTO parameter without `@Valid`. Bean validation is ignored. Add `@Valid`.
- JAVA-API-3 [high] Endpoint path, response field or status changed. Clients break. Version it.

## Security

- JAVA-SEC-1 [critical] SQL or JPQL built by concatenation, including native `@Query`. It allows injection. Use bound parameters.
- JAVA-SEC-2 [critical] `ObjectInputStream.readObject` on untrusted data, or Jackson default typing. It allows code execution. Deserialize JSON to concrete types.
- JAVA-SEC-3 [high] XML parser with DTDs enabled. It allows XXE. Set `disallow-doctype-decl`.
- JAVA-SEC-4 [critical] Endpoint missing the `@PreAuthorize` or filter-chain rule siblings have, or `permitAll` widened. It allows unauthorized access. Restore it.
- JAVA-SEC-5 [high] `Runtime.exec` with a concatenated string, or `Random` for tokens. It allows injection or guessing. Use `ProcessBuilder` arguments and `SecureRandom`.
