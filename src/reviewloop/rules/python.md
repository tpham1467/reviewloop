---
stack: python
applies_to: ["**/*.py", "**/*.pyi"]
detect: ["pyproject.toml:[project]", "pyproject.toml:[tool.poetry]", "requirements.txt:", "manage.py:", "setup.py:setup(", "setup.cfg:[metadata]", "Pipfile:[packages]"]
---

# Python rules

## Correctness

- PY-1 [high] Mutable default argument (`def f(items=[])` or `={}`). The default is shared across calls and accumulates state. Default to `None` and create inside.
- PY-2 [high] Indexing or attribute access on a value that can be `None`, such as from `.get()` or `re.match()`. It raises at runtime. Check before use.
- PY-3 [medium] `if value:` used where `0`, `""` or an empty list is valid. Valid input takes the empty branch. Compare with `is None`.
- PY-4 [medium] Naive `datetime.now()` or `utcnow()` compared with aware datetimes, or stored without a zone. It raises or shifts by hours. Use `datetime.now(tz=UTC)`.
- PY-5 [medium] Float arithmetic for money. Rounding errors accumulate. Use `Decimal` with an explicit context.
- PY-6 [medium] Iterating a dict or list while adding or removing items. It raises or skips items. Iterate over a copy.
- PY-7 [low] Late binding in closures created in a loop (`lambda: i`). Every closure sees the last value. Bind with a default argument.

## Errors and resources

- PY-ERR-1 [medium] Bare `except:` or `except Exception: pass`. It hides bugs and catches `KeyboardInterrupt` (bare form). Catch specific exceptions and handle or re-raise.
- PY-ERR-2 [low] Re-raising a new exception without `from err`. The original traceback is lost. Use `raise New(...) from err`.
- PY-ERR-3 [medium] File, socket, lock or DB connection opened without `with`. It leaks on exceptions. Use a context manager.
- PY-ERR-4 [medium] `requests` or `httpx` call without `timeout=`. It can hang forever. Always pass a timeout.
- PY-ERR-5 [medium] `assert` used to validate input or enforce permissions. Asserts are removed under `-O`. Raise a real exception.

## Concurrency

- PY-ASYNC-1 [high] Blocking call (`time.sleep`, `requests`, sync DB driver, file I/O) inside `async def`. It blocks the event loop for every request. Use the async library or `asyncio.to_thread`.
- PY-ASYNC-2 [high] Coroutine called without `await`, or `asyncio.create_task` result not kept. The work never runs or is garbage-collected mid-flight. Await it or keep a reference.
- PY-ASYNC-3 [medium] Shared mutable module-level state modified from threads or tasks without a lock. Updates are lost. Use a lock or pass state explicitly.

## Security

- PY-SEC-1 [critical] SQL built with f-strings, `%` or `.format`. It allows injection. Pass parameters to `execute(sql, params)`.
- PY-SEC-2 [critical] `subprocess` with `shell=True` and interpolated input, or `os.system`. It allows command injection. Pass an argument list without a shell.
- PY-SEC-3 [critical] `pickle`, `marshal`, `shelve` or `yaml.load` without `SafeLoader` on untrusted data. It executes arbitrary code. Use JSON or `yaml.safe_load`.
- PY-SEC-4 [critical] `eval` or `exec` on input. It executes arbitrary code. Use `ast.literal_eval` or a parser.
- PY-SEC-5 [high] Path joined from input and opened without resolving and checking it stays under a base directory; `tarfile.extractall` without a filter. It allows traversal. Resolve and check `is_relative_to`; use `filter="data"`.
- PY-SEC-6 [high] `random` used for tokens or passwords. It is predictable. Use `secrets`.
- PY-SEC-7 [high] `verify=False` on HTTPS requests. It allows man-in-the-middle. Keep verification; configure the CA bundle.

## API

- PY-API-2 [medium] Pydantic or dataclass model field renamed or made required in a serialized payload. Stored data and clients fail validation. Add an alias or a default.
