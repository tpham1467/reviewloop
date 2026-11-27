# ADR 0002: Restricting the Codex and Cursor CLIs to the review tool set

Status: accepted (milestone 1). Date: 2026-11-28.

## Context

Spec 8.2 limits a review agent to read tools, the shell commands `git log`, `git blame`, `git show` and `git diff`,
and one writable file, `.reviewloop/out/<step>.json`. Each adapter maps that to its CLI's own permission mechanism.
reviewloop also enforces isolation itself, whatever the CLI does (see `src/reviewloop/agents/isolation.py`):

- `prepare_sandbox`: a link-free clone with no remotes, credentials, hooks or committed agent config.
- A throwaway HOME per run, deleted afterwards.
- The git guard (`install_git_wrapper`) first on PATH. The guard, not the CLI, enforces git options and paths
  (ruling R17).
- An allowlist-scrubbed environment: the adapter's own model credential, `PATH`, `HOME`, and locale/temp variables.
- A snapshot check after every run: any change besides the output file fails the step.
- A process-group kill on timeout and after exit.
- `strip_checkout_credentials` (ruling R31): before the first agent run, the init step removes git credentials from the
  CI job's own checkout and from every submodule gitdir under `.git/modules/`:
  - userinfo in remote and push URLs and in proxy URLs (`http.proxy`, `http.<url>.proxy`, `remote.*.proxy`);
  - `url.<base>` sections whose name carries userinfo, and every `insteadOf`/`pushInsteadOf` rewrite;
  - `http.*.extraheader`, `credential.*`, includes, `core.askPass` and `core.sshCommand`.

  reviewloop talks to the VCS through API tokens and never fetches or pushes, so the job needs none of them after
  checkout.

The spec says a CLI that cannot be held to this tool set moves to milestone 1.5 instead of shipping weaker. The
controller's rule for this task: an adapter that cannot restrict shell commands to the allowlist is marked
`experimental = True`. `get_adapter` refuses it unless `REVIEWLOOP_EXPERIMENTAL_AGENTS=1`.

## Method

- Cursor: `cursor-agent` 2026.08.11-e8db854 is installed locally. Sources were `cursor-agent --help`, the official CLI
  docs (headless, permissions, configuration, authentication, run modes) and a read of the installed bundle to confirm
  how `permissions` and `--trust` behave. One run without a credential confirmed that print mode exits at once with
  "Authentication required ... set CURSOR_API_KEY".
- Codex: not installed. The pinned version is `@openai/codex` 0.172.0 (`npm view`). Sources were the official docs
  (non-interactive mode, CLI reference, sandbox and approvals, permissions, rules, AGENTS.md, authentication, config
  reference) and the public source at tag `rust-v0.172.0`: `exec/src/cli.rs`, `login/src/auth/manager.rs`,
  `core/src/agents_md.rs`, `core/src/exec_policy.rs` and `linux-sandbox/src/bwrap.rs`.
- Neither CLI was run against a model, because no credential was available.

## Codex (`codex exec`): experimental

### Headless mode

`codex exec ... -` reads the prompt from stdin. `--json` streams JSONL events to stdout, which becomes reviewloop's
transcript. `--output-last-message <file>` writes the final agent message to a file, and Codex writes it itself,
outside the sandbox. `--ephemeral` keeps rollout files off disk.

### Writes

`--sandbox read-only` makes every model-generated command read-only. On Linux this is bwrap plus seccomp; on macOS it is
Seatbelt. `-c approval_policy="never"` means no escalation is ever granted: a command that needs more than the sandbox
is refused (`AskForApproval::Never` maps to `Forbidden`).

The output file must still be written. The adapter gets it through `--output-last-message .reviewloop/out/<step>.json`,
so the sandbox stays read-only. The model is told that its final message is the JSON document. Writable roots are not
widened (no `--add-dir`, no `workspace-write`), because `workspace-write` would make the whole copy writable. The
snapshot check stays the hard guarantee.

`apply_patch` edits run under the same read-only policy.

### Shell commands

Codex cannot limit commands to the allowlist. In the read-only sandbox any command runs without approval (`cat`, `ls`,
`rg`, `find`, a script interpreter). Execpolicy `.rules` (`prefix_rule`) decide only which commands may run outside the
sandbox, or whether to prompt or forbid. No rule means "run in the sandbox", not "deny", and the docs mark rules as
experimental. A `forbidden` rule for every other program is not possible either: rules match exact argv prefixes, not
"everything except".

`features.shell_tool = false` removes command execution entirely. That would also remove `git log/blame/show/diff`,
which the review needs.

The git guard still holds `git` itself: the scrubbed shell PATH starts with the guard (`shell_environment_policy` sets
`PATH` to the guard-first PATH and `allow_login_shell=false`), so `git` resolves to the guard. Other programs are not
held, and git called by its absolute path skips the guard. Codex has no pattern deny to stop that.

Codex is therefore `experimental = True`.

### Reads and the credential

The read-only sandbox can read the whole filesystem by default. Permission profiles (`default_permissions` with
`:minimal` plus workspace roots) can narrow reads, but they are beta. They also do not combine with `--sandbox`: setting
`sandbox_mode` anywhere switches profiles off. The adapter keeps `--sandbox read-only`.

What limits the damage of a full read:

- The model credential reaches only the Codex process, never sandboxed commands:
  `shell_environment_policy.inherit = "none"` and `set` give commands `PATH` and `HOME` only.
- bwrap unshares the PID namespace and mounts a fresh `/proc`, so `/proc/<pid>/environ` of the runner's processes is
  not visible.
- The CI runner's own files remain readable (see "Runner files an agent can reach" below).

This is the other reason Codex is experimental.

### Project config in the untrusted checkout

- `.codex/` (config.toml, hooks, rules) loads only for a trusted project. The adapter never passes `--trust`, and
  `--ignore-rules` skips rules anyway. `prepare_sandbox` also deletes every `.codex/` directory from the copy.
- `.agents/skills/` in the copy would be loaded as repository skills, so `prepare_sandbox` deletes every
  `.agents/skills` directory. The rest of `.agents/` stays. reviewloop's own skills go to `$HOME/.agents/skills` in the throwaway HOME (the USER scope).
- `AGENTS.md`: `-c project_doc_max_bytes=0` makes Codex load no project instructions. The file stays in the copy as
  data.
- User config: `--ignore-user-config` skips `$CODEX_HOME/config.toml`. CODEX_HOME is not passed, so it defaults to
  the throwaway `$HOME/.codex`.
- Also turned off: `web_search="disabled"`, `features.apps=false` (connectors), `features.multi_agent=false`, and
  `features.shell_snapshot=false` (which copies a login shell's environment into commands). No MCP server is
  configured.

### Network

The sandbox gives commands no network by default (the Linux sandbox unshares the network). Web search is disabled. The
Codex process itself talks to the model API.

### Credentials

- `OPENAI_API_KEY`: `codex exec` reads an API key only from `CODEX_API_KEY` (`read_codex_api_key_from_env`; the
  built-in `openai` provider has no `env_key`). The adapter therefore passes the value of `OPENAI_API_KEY` as
  `CODEX_API_KEY` and passes no other variable.
- `CODEX_AUTH_JSON`: its contents are written to `$HOME/.codex/auth.json` in the throwaway HOME, directory 700 and
  file 600 (`O_EXCL`), with `cli_auth_credentials_store="file"`. The variable itself is never in the agent's
  environment, and the value is never logged.
- A refreshed ChatGPT token is lost with the throwaway HOME. The docs say a rotating refresh token must be re-saved;
  users of auth.json must refresh the secret themselves.
- Not supported: a `codex login` session in the runner's real HOME.

### Argv

```
codex exec --sandbox read-only --json --ephemeral --ignore-user-config --ignore-rules
  --output-last-message .reviewloop/out/<step>.json
  -c approval_policy="never" -c allow_login_shell=false -c project_doc_max_bytes=0 -c web_search="disabled"
  -c cli_auth_credentials_store="file" -c shell_environment_policy.inherit="none"
  -c shell_environment_policy.set={PATH = "<home>/bin:...", HOME = "<home>"}
  -c features.apps=false -c features.multi_agent=false -c features.shell_snapshot=false [--model M] -
```

## Cursor (`cursor-agent -p`): experimental

### Headless mode

`cursor-agent -p --output-format stream-json` reads the prompt from stdin when no prompt argument is given and stdin is
not a TTY (`build-prompt.ts` in the bundle). Print mode cannot stop to ask, so anything that would need approval is
rejected. Without `--force` it runs only:

- file edits inside the workspace;
- shell commands on the allowlist, or commands that fit the sandbox when it is on;
- what `permissions.allow` covers.

`--force`, `--yolo` and `--approve-mcps` are never passed.

`--trust` is required: print mode exits on an untrusted workspace. The trust mark is stored under
`$HOME/.cursor/projects/...`, which is the throwaway HOME, so nothing is written into the copy.

### Shell commands

`~/.cursor/cli-config.json` in the throwaway HOME sets `"approvalMode": "allowlist"` and
`permissions.allow = ["Shell(git log)", "Shell(git blame)", "Shell(git show)", "Shell(git diff)"]`. How matching
works:

- Cursor parses each command with tree-sitter-bash and checks every executable command against the allowlist.
- `Shell(git log)` matches `git log` and `git log ...`, a literal prefix of the command text.
- If the parser fails, finds no command, or sees a redirect to anything but `/dev/null` or a workspace or temp file, the
  command needs approval, which print mode rejects.
- When the server-side flag `smartAllowlistEnabled` is on, the matcher works on the classifier's command output
  instead of the parser's, so matching can change without any client change.

Cursor cannot hold shell commands to the allowlist. The review of PR #14 confirmed this with the bundled tree-sitter
parser and matcher:

- Assignments are dropped. The matcher keeps neither `VAR=value` prefixes nor standalone assignments, so it treats
  these as plain `git ...` and allows them:
  - `PATH=/usr/bin:/bin git diff --no-index /etc/hostname /dev/null`
  - `GIT_EXTERNAL_DIFF='sh -c ...' PATH=... git log -p --ext-diff -1`
  - `export PATH=...; git log`

  Each one puts the real git ahead of the guard, or runs an arbitrary program through git. That program has
  `CURSOR_API_KEY` in its env and, with the sandbox off, open network.
- Login-shell state is replayed. Cursor dumps and replays a login shell's state (`bash -ilc`, `zsh -ilc` in the
  bundle). On images whose `/etc/profile` sets PATH, that can put the real git ahead of the guard, even for a plain
  `git log`.

Neither can be fixed from reviewloop's side through Cursor's permission config.

Possible future path (not built; it needs a real smoke run first): a user-level `~/.cursor/hooks.json` in the throwaway
HOME with `beforeShellExecution` and `preToolUse` hooks. These would run a reviewloop `python -I` checker on the raw
command text and reject anything that is not a bare `git log|blame|show|diff` with no assignments, expansions or
operators. The smoke run must confirm three things: the hook sees the raw text before Cursor's parsing, a denial is
final in print mode, and project hooks stay ignored.

`--sandbox disabled` is passed and `sandbox.mode` is `"disabled"`. With the sandbox on, the allowlist would stop
mattering: in allowlist mode a sandboxed command is "preapproved" whenever its sandbox policy fits, whatever the
command. With the sandbox off, the allowlist is the only way a shell command runs.

`permissions.deny` (deny wins) adds an outer layer only:

- git by path or through a launcher would skip the guard: `Shell(*/git)`, `Shell(env)`, `Shell(command)`,
  `Shell(exec)`.
- `$` or backtick expansion and `/proc` could print the credential: `Shell(*$*)`, ``Shell(*`*)``, `Shell(*/proc/*)`.

`Shell(*/git *)` and `Shell(*git-*)` were dropped: they also denied allowed git commands that name a real path
containing `git-` or ending in `/git`.

Cursor is therefore `experimental = True`.

### Writes

Cursor cannot limit edits to one file: print mode edits workspace files without approval. `Write(...)` rules are
allow/deny lists over the same default. The protected paths `.git/hooks` and `.vscode` are refused, and writes outside
the workspace need approval, so they are rejected. Any write other than the output file is caught by the snapshot
check and fails the step. That is the guarantee spec 8.2 names for writes.

### Reads

With the sandbox off, Cursor's Read, Grep and Glob tools read any absolute path. `readBoundary: "workspace"` applies only
to sandboxed commands, and a team `readBoundary` only to the sandbox and shell auto-approval; the bundle's `shouldBlockRead`
does not check it. The config still sets it, in case a later version extends it.

`permissions.deny` therefore names the runner files that hold credentials:

- `Read(/proc/*)`
- `Read(*/.git/config)` and `Read(*/.git/modules/*)`
- `Read(*/.git-credentials)` and `Read(*/.netrc)`
- `Read(*/.ssh/*)`
- `Read(*/.docker/config.json)`
- `Read(*/.config/gh/*)` and `Read(*/.config/glab-cli/*)`

These rules match the path after `~` expansion, not after symlink resolution, so they are best effort. The model
credential is not on disk; `/proc/self/environ` of the agent holds it, and `/proc` reads are denied.

See "Runner files an agent can reach" below.

### Project config in the untrusted checkout

`cursor-agent` reads these project files:

- `.cursor/cli.json` (project permissions) from every directory between the git root and the cwd;
- `.cursor/permissions.json`, `.cursor/sandbox.json`, `.cursor/hooks.json` and `.cursor/mcp.json`;
- `.cursor/rules/**/*.mdc`, `.cursor/skills`, `.cursor/commands` and `.cursorrules`;
- `.cursorignore`, which hides files from the agent and would let a PR hide code from review;
- `.claude/settings.json` hooks and `.claude/commands`, and `.agents/skills` and `.codex/skills`.

`prepare_sandbox` now deletes, at any depth: `.cursor/`, `.codex/`, `.agents/skills` and `.claude/` directories, and the
`.mcp.json`, `.cursorrules`, `.cursorignore` and `.cursorindexingignore` files. Deleted tracked files are marked
skip-worktree, so `git diff` shows no fake deletions.

`AGENTS.md`, `CLAUDE.md` and `CLAUDE.local.md` stay as data. Cursor does load them as rules, and there is no flag to stop
that. The output-contract skill tells the agent to treat all repository text as data.

User-level hooks, MCP and settings come from the throwaway HOME, which holds only `cli-config.json` and the step's
skills.

### Network

The sandbox is off, so allowlisted git commands could reach the network. The sandbox's git copy has no remotes, and
the guard allows only four read subcommands. `WebFetch(*)` and `Mcp(*:*)` are denied. Web search needs approval
(`autoAcceptWebSearch: false`), which print mode rejects.

### Credentials

`CURSOR_API_KEY` only. `--api-key` is never used, so the key stays out of argv. A `cursor-agent login` session is not
used, because HOME is the throwaway directory. `CURSOR_API_ENDPOINT` and `CURSOR_CONFIG_DIR` are not passed.

### Argv

```
cursor-agent -p --output-format stream-json --trust --sandbox disabled [--model M]
```

## Runner files an agent can reach

Codex and Cursor both let the agent read absolute paths outside the copy. A CI runner holds credentials in files:

- **GitLab checkout.** By default GitLab Runner clones from `CI_REPOSITORY_URL`, which contains
  `gitlab-ci-token:<CI_JOB_TOKEN>@`, and keeps it as the `origin` URL in the job checkout's `.git/config`. The job
  token is on disk unless `strip_checkout_credentials` runs first.
- **GitHub checkout.** `actions/checkout` persists an `http.https://github.com/.extraheader` with the token unless
  `persist-credentials: false` is set. `strip_checkout_credentials` removes it too.
- **GitLab File-type CI variables.** These are plain files under `<builds>/<project>.tmp/`, next to the checkout.
  reviewloop cannot remove them, because the job may need them. An agent that can read absolute paths can open them.

This is the reason Codex and Cursor stay experimental. It is also why the Claude Code adapter relies on
`blockReadsOutsideWorkingDirectories`; the real-CLI smoke run must confirm that a read of `../<project>.tmp/<var>` is
refused.

## Decision

- `CodexAdapter.experimental = True`. `get_adapter("codex")` raises `ConfigError` unless
  `REVIEWLOOP_EXPERIMENTAL_AGENTS=1`. The full Codex adapter moves to milestone 1.5: it needs permission profiles
  (`:minimal` reads plus the copy, read-only) to leave beta and to combine with a command restriction, or an execpolicy
  mode where an unmatched command is denied.
- `CursorAdapter.experimental = True`, gated the same way. The full Cursor adapter moves to milestone 1.5, through the
  hook-based checker described above or a Cursor matcher that keeps assignments.
- In milestone 1, only Claude Code is available without the opt-in.
- `prepare_sandbox` removes Codex and Cursor project config, as listed above. For `.agents/`, only `.agents/skills` is
  removed.
- `strip_checkout_credentials` runs on the source checkout before any agent run.

## Smoke checks before relying on a real run

With a real credential:

1. Codex writes valid JSON to the `--output-last-message` file, and `--json` events reach the transcript.
2. Codex `git log` resolves to the guard.
3. Cursor `git log -n 1` runs.
4. Cursor `cat app.py`, `ls`, `/usr/bin/git diff a /abs/x` and `git log > x` are rejected in print mode.
5. Cursor creates `.reviewloop/out/<step>.json` without approval.
6. Cursor `Read(/proc/self/environ)` is refused.
7. Claude Code refuses a read of a GitLab File-type variable under `../<project>.tmp/`
   (`blockReadsOutsideWorkingDirectories`).
