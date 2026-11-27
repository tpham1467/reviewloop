# Contributing

Thanks for helping with reviewloop.

## Setup

```bash
uv sync
uv run pre-commit install
```

## Before you open a pull request

```bash
uv run pytest
uv run pyright
uv run ruff check
uv run ruff format --check
```

- Use Conventional Commits for commit messages and the PR title (`feat:`, `fix:`, `docs:`, `chore:`).
- Keep changes focused and add tests. Coverage must stay at or above the project threshold.
- Do not commit secrets or content you do not have the right to publish.
- Sign the CLA when the CLA bot asks (see [CLA.md](CLA.md)).
- By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
