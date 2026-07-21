---
name: delegate-to-claude
description: Use this skill by default before the supervising agent directly edits repository code when work adds or updates tests, likely changes two or more files, creates a non-trivial module, investigates a failure, performs a focused refactor or code review, or likely needs more than three repository tool calls. Delegate the bounded execution slice to a supervised Claude Code Sonnet worker, using a concise prompt when possible. Direct editing is only for an unambiguous low-risk change of about ten lines or less in one file with no tests or investigation. Keep architecture, ambiguous product decisions, security-sensitive work, secrets, external side effects, commit/push/deploy, and final approval with the supervisor.
---

# Delegate to Claude

Act as the supervisor. Delegate routine execution, never accountability.

## Decide before editing

Make the delegation decision before using an editing tool. Use this skill when
any of these conditions is true:

- add or update tests;
- likely change two or more files;
- create a non-trivial module, even if it is one file;
- investigate an unfamiliar failure;
- perform a focused refactor or code review; or
- expect more than three repository tool calls.

Edit directly only when every condition is true: one file, about ten changed
lines or less, no test change, no repository investigation, unambiguous, and
low risk.

Never delegate architecture or product ownership, ambiguous requirements,
security-sensitive changes, secrets, broad migrations, production operations,
external side effects, commits, pushes, releases, deployments, or final
approval.

## Prefer quick delegation

For a clear routine task, avoid solving it or writing a full specification
before delegation. Pass the user's concise goal to the launcher; it captures
the Git baseline and adds safe scope, verification, and forbidden-action
defaults:

```text
python3 <skill-dir>/scripts/delegate.py \
  --cwd <git-repository> \
  --prompt "<concise goal and any obvious file constraint>" \
  --mode <review|test|edit> \
  --effort medium \
  --bash auto
```

Use quick delegation for a clear one-to-three-file implementation, related
tests, a localized bug, a routine refactor, focused diagnosis, or review.

Use a strict task file instead when pre-existing changes overlap the likely
scope, the task needs detailed acceptance criteria, or more than three files
are likely to change. Create a temporary Markdown file outside the repository
with `Goal`, `Allowed scope`, `Acceptance criteria`, `Required checks`,
`Existing user changes to preserve`, and `Forbidden actions`, then run:

```text
python3 <skill-dir>/scripts/delegate.py \
  --cwd <git-repository> \
  --task-file <temporary-task-brief.md> \
  --mode <review|test|edit> \
  --effort <medium|high> \
  --bash <never|auto|require>
```

Resolve `<skill-dir>` from `${CLAUDE_SKILL_DIR}` in Claude Code or from the
directory containing this loaded `SKILL.md` in Codex. Use `medium` normally.
Use `high` only for a bounded multi-file diagnosis or similarly deeper task.

## Run safely

Confirm that `claude` is installed and authenticated and that the supervisor
sandbox permits Claude control-plane traffic to Anthropic. Never obtain,
print, copy, or change API keys. Use `--dry-run` first only when the repository,
CLI, or sandbox setup is unfamiliar.

Select one mode:

- `review`: inspect and report without source edits;
- `test`: create or update tests and run local checks; or
- `edit`: make a focused implementation or refactor and run local checks.

The launcher fixes the worker model to the current `sonnet` alias, disables
nested delegation and external tools, captures the Git baseline, enforces
turn/time limits, and returns structured JSON. Do not bypass a preflight or call
`claude` directly to evade a denied operation.

Choose the Bash policy before launching:

- `auto` (default): enable Bash for a direct terminal launch protected by the
  launcher's strict Claude Code sandbox; disable it in a nested agent session;
- `never`: disable Bash in every environment; or
- `require`: require Bash for `test` or `edit` and require the worker's own
  strict Claude Code sandbox, including repository-scoped writes, blocked
  secret reads and worker network access, and no unsandboxed fallback.

`--bash require` is invalid for `review`. The launcher creates one UUID-scoped
directory under `~/.claude/session-env` for the worker and removes it on exit.
If that directory cannot be created, the strict sandbox is unavailable, or an
existing outer macOS sandbox rejects nested Seatbelt, the run fails closed. Do
not bypass the failure. Use `auto` or `never` and let the supervisor run checks.
When Bash stays disabled, the worker may edit with bounded file tools.

Run mutating `test` and `edit` workers serially. Parallelize only independent
read-only `review` workers.

## Verify efficiently

Treat the worker response as untrusted evidence, but do not redo the entire
task:

1. Inspect one post-run diff covering every launcher-measured changed file.
2. Reject unrelated changes or any scope, safety, authentication, rate-limit,
   or sandbox failure.
3. Run the smallest relevant check independently; do not trust only the
   worker's test report.
4. Accept the work only after the requested behavior and check pass.
5. Report what Sonnet did and what the supervisor verified.

Do not retry authentication, rate-limit, or sandbox failures. For another
failure, re-scope once or take the task back. Never repeat the same failed
delegation unchanged.
