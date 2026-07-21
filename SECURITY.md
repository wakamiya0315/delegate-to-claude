# Security Policy

## Project status

`delegate-to-claude` is experimental and macOS-first. Its sandboxing,
permission rules, structured output, and supervisor verification are
defense-in-depth controls; they are not a production security boundary.

The supervisor must retain security-sensitive decisions, secret handling,
external side effects, publication, deployment, and final approval. Do not use
this project to process untrusted secrets or to authorize production changes.

## Supported versions

Security fixes are provided for the latest published release. Upgrade to the
latest release before reporting an issue that may already be fixed.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting for this repository:

<https://github.com/wakamiya0315/delegate-to-claude/security/advisories/new>

Do not open a public issue for a suspected vulnerability. Include:

- the affected release and operating system;
- the supervisor and Claude Code versions;
- the mode and effort used;
- minimal reproduction steps;
- the expected and observed sandbox or permission boundary; and
- whether repository files, credentials, network access, or external state were
  affected.

Do not include real credentials, API keys, private source code, or sensitive
worker transcripts. Replace them with minimal synthetic fixtures.

## Scope

Useful reports include sandbox escape, command-policy bypass, unintended
network access, secret exposure, unsafe installer overwrite, receipt data
leakage, scope enforcement failure, and worker recursion.

General model-quality disagreements, expected Claude quota consumption, and
fail-closed behavior caused by unavailable authentication, sandboxing, or rate
limits are not security vulnerabilities by themselves.

## Bash policy

Version 0.2.1 adds `--bash never|auto|require`. `require` is available only for
`test` and `edit`; it requires the worker's own strict Claude Code sandbox and
never treats a supervisor assertion or an unknown outer boundary as sufficient.
The launcher fails closed if the sandbox or its isolated UUID-scoped session
environment cannot be created. On macOS, this includes an existing Seatbelt
sandbox rejecting a nested sandbox. Do not work around that failure with
`bypassPermissions` or unsandboxed execution.
