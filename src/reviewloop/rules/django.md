---
stack: django
applies_to: ["**/*.py", "**/templates/**/*.html"]
detect: ["requirements.txt:django", "requirements.txt:Django", "pyproject.toml:django", "pyproject.toml:Django", "Pipfile:django", "setup.py:Django", "manage.py:DJANGO_SETTINGS_MODULE"]
---

# Django rules

## Queries

- DJ-1 [medium] Related object accessed in a loop or template over a queryset without `select_related` (foreign key) or `prefetch_related` (many). It causes N+1 queries. Add the matching call to the queryset.
- DJ-2 [medium] `len(qs)` or `if qs:` to test existence or count. It loads every row. Use `.exists()` or `.count()`.
- DJ-3 [high] `Model.objects.get()` without handling `DoesNotExist` and `MultipleObjectsReturned`, or `.first()` result used without a None check. It returns 500. Use `get_object_or_404` in views or handle both cases.
- DJ-4 [medium] Queryset or list view without pagination or slicing. Response size grows with the table. Paginate.
- DJ-5 [high] Read, modify in Python, then `save()` for counters or balances. Concurrent requests lose updates. Use `F()` expressions or `select_for_update()` inside a transaction.
- DJ-6 [medium] `save()` without `update_fields` in code that runs concurrently with other writers. It overwrites fields changed by others. Pass `update_fields`.

## Transactions

- DJ-TX-1 [high] Several writes in a view or service without `transaction.atomic()`. A failure leaves partial data. Wrap them in `atomic()`.
- DJ-TX-2 [high] Email, task enqueue or external call inside `atomic()` that depends on the committed data. It runs even when the transaction rolls back, or before the row is visible to the worker. Use `transaction.on_commit`.
- DJ-TX-3 [medium] Catching `IntegrityError` inside `atomic()` without a nested savepoint. The outer transaction is broken. Wrap the risky statement in its own `atomic()` block.

## Migrations

- DJ-MIG-1 [critical] Migration removes or renames a field still read by deployed code, or `RunPython` without a reverse. Rolling deploys fail and data is lost. Split into additive steps and provide `reverse_code`.
- DJ-MIG-2 [high] Adding a non-null column with a default, or an index, on a large table in one migration. It locks the table. Add nullable, backfill in batches, then constrain; use concurrent index creation on PostgreSQL.
- DJ-MIG-3 [medium] Model change without a matching migration, or a data migration importing models directly. Schema drifts or the migration breaks later. Run `makemigrations`; use `apps.get_model`.

## Security

- DJ-SEC-1 [critical] `raw()`, `extra()`, `RawSQL` or `cursor.execute` with string-formatted input. It allows SQL injection. Pass `params`.
- DJ-SEC-2 [critical] `mark_safe`, `|safe`, `format_html` misuse, or `{% autoescape off %}` with user data. It allows XSS. Keep autoescaping; use `format_html` with arguments.
- DJ-SEC-3 [high] `@csrf_exempt` on a cookie-authenticated view that changes state. It allows CSRF. Remove it or use token authentication.
- DJ-SEC-4 [critical] View that changes or reads private data without `login_required`, `permission_required` or a DRF permission class, or object fetched by `pk` without filtering by owner. It allows unauthorized access and IDOR. Add the check and filter by `request.user`.
- DJ-SEC-5 [high] `ModelForm` with `fields = "__all__"` or `exclude`, or a DRF serializer with all fields writable. Users set protected fields. List fields explicitly; mark others read-only.
- DJ-SEC-6 [critical] `DEBUG = True`, a hard-coded `SECRET_KEY`, or `ALLOWED_HOSTS = ["*"]` in production settings. Secrets and stack traces leak. Read from the environment.
- DJ-SEC-7 [high] Redirect to a URL from the request without `url_has_allowed_host_and_scheme`. It is an open redirect. Validate the target.

## DRF and API

- DJ-API-1 [high] Serializer field renamed, removed or changed type on an existing endpoint. Clients break. Version or keep the field.
- DJ-API-2 [medium] `get_queryset` not filtered by user while permissions only check authentication. Users list other users' data. Filter the queryset.
- DJ-API-3 [medium] Signals used for core business writes. Bulk operations skip them and order is hidden. Call the logic explicitly.
