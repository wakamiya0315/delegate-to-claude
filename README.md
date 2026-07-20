# delegate-to-claude

[日本語](README_jp.md)

[![CI](https://github.com/wakamiya0315/delegate-to-claude/actions/workflows/ci.yml/badge.svg)](https://github.com/wakamiya0315/delegate-to-claude/actions/workflows/ci.yml)

> [!WARNING]
> **Experimental / macOS-first.** This project is an early release, not a
> production security boundary. Automatic delegation sends requests through
> the user's existing Claude Code account, consumes Claude quota and rate
> limits, and may incur charges under that account's plan or API configuration.
> Version 0.2 delegates routine coding more proactively, so review your usage
> after upgrading.

`delegate-to-claude` is a cross-compatible Agent Skill that lets Codex or
Claude Code remain the supervisor while delegating bounded repository work to
a fresh Claude Code Sonnet worker.

The worker may implement a small change, create or run tests, diagnose a
failure, or review code. A concise quick prompt is enough for routine work; the
launcher adds the safety and verification defaults. The supervisor still owns
task scoping, diff review, independent verification, and final approval.

## Why use it

Flagship models are valuable for architecture, ambiguous decisions, and final
review, but using them for every mechanical step consumes their token and rate
limits. The skill now asks the supervisor to delegate before editing when work
adds tests, likely changes multiple files, creates a non-trivial module,
investigates a failure, performs a focused refactor or review, or is likely to
need more than three repository tool calls.

Direct editing remains appropriate only for an unambiguous, low-risk change of
about ten lines or less in one file that needs no test update or investigation.

It deliberately keeps architecture, product judgment, security-sensitive
changes, broad migrations, secrets, external side effects, and final approval
with the supervisor.

## Requirements

- macOS, or Linux with the Claude Code sandbox dependencies installed
- Claude Code 2.1.205 or newer, already authenticated
- Python 3.9 or newer
- Git
- Codex and/or Claude Code with Agent Skills support
- supervisor permission for Claude Code control-plane traffic to Anthropic

The launcher uses your existing Claude Code login. It does not obtain, print,
store, or modify API keys. Worker tools remain unable to access the network;
the control-plane permission only lets the `claude` process call Anthropic.

## Install

Clone the repository into a permanent location, then create personal skill
symlinks:

```bash
git clone https://github.com/wakamiya0315/delegate-to-claude.git
cd delegate-to-claude
python3 scripts/install.py --target both
```

Install for one supervisor only with `--target codex` or `--target claude`.
Preview all filesystem changes with `--dry-run`.

The installer links the same source directory into:

- `~/.agents/skills/delegate-to-claude` (current Codex user location)
- `~/.codex/skills/delegate-to-claude` (legacy Codex compatibility)
- `~/.claude/skills/delegate-to-claude`

It refuses to overwrite an existing file, directory, or different symlink.
After a first installation, restart Codex. Restart Claude Code as well if its
personal skills directory did not exist when the session started. Future
repository updates are visible through the symlinks.

## Update

Review the release notes and changes before updating, then fast-forward the
permanent clone and re-run the idempotent installer:

```bash
git pull --ff-only
python3 scripts/install.py --target both
```

Because the installed paths are symlinks, checked-out changes become active in
new Codex and Claude Code sessions immediately. Pin a release tag instead of
following `main` when reproducibility is more important than automatic updates.

## Uninstall

Remove only the installed symlinks. The commands below refuse to remove a real
file or directory at those paths:

```bash
for link in \
  "$HOME/.agents/skills/delegate-to-claude" \
  "$HOME/.codex/skills/delegate-to-claude" \
  "$HOME/.claude/skills/delegate-to-claude"
do
  if [ -L "$link" ]; then
    unlink "$link"
  fi
done
```

After verifying that the links are gone, remove the permanent clone separately
if it is no longer needed.

## Use from Codex

Invoke the skill explicitly with a concise task:

```text
Use $delegate-to-claude to add CSV validation to src/parser.py and update its tests.
```

Codex may also select the skill automatically. The description is tuned to
prefer delegation before routine multi-file, test-writing, diagnosis, refactor,
and review work. Implicit selection is still a model decision rather than a
hard enforcement mechanism.

For a non-interactive `codex exec` run under `workspace-write`, allow the
Claude control-plane connection. Add the receipt cache directory when you want
the run metadata to persist without a warning:

```bash
codex exec --sandbox workspace-write \
  -c sandbox_workspace_write.network_access=true \
  --add-dir ~/Library/Caches/delegate-to-claude \
  'Use $delegate-to-claude to review this focused change.'
```

## Use from Claude Code

Invoke the same skill explicitly:

```text
/delegate-to-claude add CSV validation to src/parser.py and update its tests
```

Claude Code may also load the skill automatically under the same balanced
delegation criteria. Even when Claude Code is the supervisor, the skill starts
a separate non-interactive `claude -p` process so the worker has fresh context
and a fixed Sonnet model.

## Direct launcher usage

For routine work, pass one concise quick prompt. The launcher synthesizes the
minimal scope, acceptance, verification, existing-change, and forbidden-action
sections:

```bash
python3 ~/.agents/skills/delegate-to-claude/scripts/delegate.py \
  --cwd /path/to/repository \
  --prompt "Add CSV validation to src/parser.py and update its focused tests." \
  --mode edit \
  --effort medium
```

Use a strict task file when pre-existing changes overlap the likely scope, more
than three files may change, or exact acceptance criteria matter. Create the
Markdown brief outside the target repository:

```markdown
# Goal
Fix the off-by-one error in the CSV row counter.

# Allowed scope
`src/csv_counter.py` and its focused tests only.

# Acceptance criteria
Empty, one-row, and multi-row inputs return the expected counts.

# Required checks
Run `python3 -m unittest tests.test_csv_counter`.

# Existing user changes to preserve
Preserve every pre-existing change reported by Git.

# Forbidden actions
No dependency changes, network access, commit, push, or unrelated cleanup.
```

Run the worker:

```bash
python3 ~/.agents/skills/delegate-to-claude/scripts/delegate.py \
  --cwd /path/to/repository \
  --task-file /path/to/task.md \
  --mode edit \
  --effort medium
```

The public arguments are:

- `--cwd`: a path inside the target Git repository; the worker runs at its root
- exactly one task input:
  - `--prompt`: a non-empty concise quick goal, at most 32 KiB
  - `--task-file`: a non-empty UTF-8 strict Markdown brief, at most 256 KiB
- `--mode review|test|edit`
- `--effort medium|high`
- `--dry-run`: validate the setup without starting a worker

`medium` allows up to 12 agent turns and 15 minutes. `high` allows up to
24 turns and 30 minutes. The model is always the current `sonnet` alias.

macOS does not support nesting Claude Code's Seatbelt sandbox inside an existing
Codex or Claude Code sandbox. When the launcher detects a nested agent session,
it inherits the outer sandbox and removes Bash from the worker tool set. The
worker can still review or edit through bounded file tools, but the supervisor
must run every command and test independently. A direct terminal launch uses
Claude Code's strict sandbox and can run local checks normally.

## Modes

| Mode | Intended work | Source editing |
| --- | --- | --- |
| `review` | Static code review and focused repository research | Disabled; any measured change fails the run |
| `test` | Test creation, test maintenance, and local verification | Enabled |
| `edit` | Small implementation or refactor plus local checks | Enabled |

Only `review` workers may run in parallel. The launcher uses a repository-scoped
lock to reject overlapping `test` or `edit` workers.

## Result contract

The launcher prints one normalized JSON object:

```json
{
  "status": "completed",
  "summary": "Implemented the bounded fix and verified the focused tests.",
  "changed_files": ["src/csv_counter.py", "tests/test_csv_counter.py"],
  "tests": [
    {
      "command": "python3 -m unittest tests.test_csv_counter",
      "outcome": "passed",
      "details": "4 tests passed"
    }
  ],
  "concerns": [],
  "recommended_next_action": "Supervisor should inspect the diff and rerun the test."
}
```

`changed_files` is measured against the Git state captured by the launcher. It
does not blindly trust the worker's report, including when files were already
dirty before delegation.

## Safety model

Every worker run:

- uses Claude Code's strict OS sandbox for direct terminal launches and a
  detected outer agent sandbox for nested launches, failing closed when neither
  boundary is available;
- denies sandbox escape and network domains;
- restricts writes to the repository, the sandbox session temp directory, and
  one UUID-scoped `~/.claude/session-env` metadata directory that the launcher
  creates for the run and removes on exit;
- blocks common credential files and environment tokens;
- disables slash commands, nested agents, MCP, and browser tools;
- blocks Claude/Codex recursion, Git mutation, commit, push, publishing, and
  deployment commands; and
- never uses `bypassPermissions`.

For a nested Codex or Claude Code invocation, the verified outer agent sandbox
replaces the inner Claude sandbox and Bash is unavailable to the worker. This
prevents an unsafe unsandboxed shell fallback while avoiding unsupported nested
Seatbelt execution.

The sandbox is a boundary, not a substitute for supervision. An edit worker can
still make an incorrect change inside the repository. Always inspect the diff
and rerun the required checks in the supervising process before accepting it.

Authentication, rate-limit, and sandbox failures are not automatically retried.
For other failures, re-scope once into a smaller task or take the task back.

## Receipts and privacy

Each real run appends a minimal JSONL receipt to:

- macOS: `~/Library/Caches/delegate-to-claude/runs.jsonl`
- Linux: `${XDG_CACHE_HOME:-~/.cache}/delegate-to-claude/runs.jsonl`

Set `DELEGATE_TO_CLAUDE_CACHE_DIR` to use another cache directory. Receipts
contain timestamp, task hash, input style (`quick` or `strict`), model, effort,
mode, duration, status, changed file paths, test command/outcome, and aggregate
usage reported by Claude Code.
They do not contain the task text, source code, worker summary, test details,
stdout, stderr, or session ID. If the cache is not writable, the run continues
and emits a warning.

## Development and validation

Run the offline suite:

```bash
python3 -m unittest discover -s tests -v
python3 /path/to/skill-creator/scripts/quick_validate.py skill/delegate-to-claude
```

The test suite uses a fake Claude executable and temporary Git repositories. It
does not consume Claude usage. Real forward tests are intentionally separate.

## Upstream references

- [Codex: Build skills](https://developers.openai.com/codex/skills/create-skill)
- [Claude Code: Extend Claude with skills](https://code.claude.com/docs/en/slash-commands)
- [Claude Code CLI reference](https://code.claude.com/docs/en/cli-usage)
- [Claude Code sandbox](https://code.claude.com/docs/en/sandboxing)

## License

MIT
