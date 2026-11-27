# AGENTS.md

Guidance for AI coding tools working in this repository.

- Everything is in English: code, comments, docs, commit messages, issues. No emoji.
- Clean-room rule: this is a public personal project. Never add any company, employer, internal host or previous
  product name, and no content copied from private code.
- Before every commit run: `uv run pytest && uv run pyright && uv run ruff check`
  (also `uv run ruff format --check`).
- Commits follow Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`).
- Never commit secrets. gitleaks runs in pre-commit and CI.
- Package manager is `uv`; dependencies live in `pyproject.toml` and `uv.lock`.
