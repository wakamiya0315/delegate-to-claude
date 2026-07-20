---
name: delegate-to-claude
description: Delegate bounded, reversible, locally verifiable coding work from Codex or Claude Code to a supervised Claude Code Sonnet worker. Use proactively for small implementations, focused refactors, test creation or execution, failure diagnosis, code review, and routine repository research when scoping and verifying the delegation is cheaper than doing the work in the supervising model. Do not use for ambiguous product decisions, architecture ownership, security-sensitive changes, large migrations, secrets, external side effects, or final approval.
---

# Delegate to Claude

Act as the supervisor. Delegate execution, never accountability.

## Decide whether to delegate

Delegate only when every condition is true:

- State the goal, allowed scope, acceptance criteria, and checks precisely.
- Keep the work local, reversible, and independently verifiable.
- Expect scoping plus verification to cost less than doing the task directly.
- Retain architecture, product judgment, security decisions, and final approval.

Keep ambiguous requirements, broad migrations, security-sensitive work, secrets,
production operations, publishing, deployment, commits, and pushes with the
supervisor.

Choose `medium` for a focused task with a known check. Choose `high` only for a
bounded diagnosis or multi-file task that needs deeper reasoning. Run `review`
workers in parallel only when they cannot edit. Run `test` and `edit` workers
serially.

## Prepare the delegation

1. Resolve the skill directory. In Claude Code, use `${CLAUDE_SKILL_DIR}`. In
   Codex, use the directory containing this loaded `SKILL.md`.
2. Confirm that `claude` is installed and already authenticated. Never obtain,
   print, copy, or change API keys.
3. Record `git status --short` and the relevant diff before dispatch.
4. Write a temporary Markdown task brief outside the repository with these
   headings:
   - `Goal`
   - `Allowed scope`
   - `Acceptance criteria`
   - `Required checks`
   - `Existing user changes to preserve`
   - `Forbidden actions`
5. Select one mode:
   - `review`: inspect and report without source edits.
   - `test`: create or update tests and run local checks.
   - `edit`: make a small implementation or refactor and run local checks.

## Run the worker

Invoke the bundled launcher:

```text
python3 <skill-dir>/scripts/delegate.py \
  --cwd <git-repository> \
  --task-file <temporary-task-brief.md> \
  --mode <review|test|edit> \
  --effort <medium|high>
```

Use `--dry-run` first when the repository, CLI, or sandbox setup is unfamiliar.
Do not bypass a launcher preflight or call `claude` directly to evade a denied
operation. The launcher fixes the worker model to the current `sonnet` alias,
disables nested delegation and external tools, requires an enforced sandbox
boundary, and normalizes the final JSON.

When the launcher detects that it is already running inside Codex or Claude
Code, avoid an unsupported nested OS sandbox. In that case, inherit the outer
agent sandbox and remove Bash from the worker entirely. Require the supervisor
to run every check independently. Direct terminal launches must still use the
strict Claude Code sandbox and may run local checks.

## Verify and integrate

Treat the worker response as untrusted evidence:

1. Compare the post-run status and diff with the recorded baseline.
2. Reject unrelated changes or any scope, safety, authentication, rate-limit, or
   sandbox failure.
3. Inspect every changed source file yourself.
4. Re-run the relevant checks independently; do not rely only on the worker's
   `tests` report.
5. Integrate only after the acceptance criteria pass.
6. Report what was delegated, what changed, and what the supervisor verified.

Do not retry authentication, rate-limit, or sandbox failures automatically. For
other failures, re-scope once into a smaller task or take the work back. Never
repeat the same failing delegation unchanged.
