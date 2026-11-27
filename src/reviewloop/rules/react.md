---
stack: react
applies_to: ["**/*.tsx", "**/*.jsx"]
detect: ["package.json:\"react\""]
---

# React rules

## Hooks

- RCT-1 [high] Hook called conditionally, in a loop, or after an early return. Hook order changes between renders and state is mixed up. Call hooks unconditionally at the top level.
- RCT-2 [medium] `useEffect`, `useMemo` or `useCallback` dependency list missing a value used inside. The callback reads stale props or state. Add the dependency or move the value inside.
- RCT-3 [medium] Effect that subscribes, sets a timer, opens a socket or adds a listener without returning a cleanup. It leaks and fires after unmount. Return a cleanup function.
- RCT-4 [medium] Async fetch in an effect that sets state without cancellation. A slow earlier response overwrites a newer one. Use `AbortController` or an ignore flag in cleanup.
- RCT-5 [medium] Effect that sets state derived from props or other state. It causes an extra render and can loop. Compute the value during render or with `useMemo`.
- RCT-6 [high] State setter called unconditionally during render or in an effect without dependencies. It causes an infinite render loop. Guard the update or move it to an event handler.

## State and rendering

- RCT-7 [high] State or props mutated in place (`push`, assignment to a field) before setting. React skips the re-render and the UI shows old data. Create a new array or object.
- RCT-8 [medium] Update based on the previous value written as `setX(x + 1)` in async or batched code. Updates are lost. Use the functional form `setX(prev => prev + 1)`.
- RCT-9 [medium] List rendered with array index or a non-unique value as `key` while items reorder, insert or delete. State attaches to the wrong row. Use a stable unique ID.
- RCT-10 [medium] Component defined inside another component's body. It remounts on every render and loses state and focus. Define it at module level.
- RCT-11 [low] `{count && <X/>}` where `count` can be `0`. It renders a literal 0. Use `count > 0 &&`.
- RCT-12 [medium] API data rendered without loading, empty and error states. Users see a crash or blank screen. Render each state.

## Security

- RCT-SEC-1 [critical] `dangerouslySetInnerHTML` with user or API content. It allows XSS. Sanitize with DOMPurify or render as text.
- RCT-SEC-2 [high] `href` or `src` from user input without checking the scheme. `javascript:` URLs execute script. Allow only `http`, `https` and relative URLs.
- RCT-SEC-3 [high] Secret key or private token in client code or a public env variable (`NEXT_PUBLIC_`, `VITE_`, `REACT_APP_`). It ships to every browser. Keep it on the server.
- RCT-SEC-4 [high] Authorization enforced only by hiding UI. Users call the API directly. Enforce it on the server.
- RCT-SEC-5 [medium] Tokens stored in `localStorage`. Any XSS reads them. Prefer httpOnly cookies.

## Server components and performance

- RCT-SSR-1 [high] Server code passes secrets or full database records to a client component. They are serialized to the browser. Pass only needed fields.
- RCT-SSR-2 [high] Server action or route handler without an authentication and authorization check. Server actions are public endpoints. Check the session and permission inside each action.
- RCT-SSR-3 [medium] `window`, `document` or `localStorage` read during render of server-rendered code. It crashes on the server or causes hydration mismatch. Read it in an effect.
- RCT-PERF-1 [low] New object or function passed to a memoized child each render. Memoization never hits. Memoize the value.
- RCT-PERF-2 [medium] Fetch per list item inside child components (request waterfall). Load time grows with list size. Fetch once at the parent.
