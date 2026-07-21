#!/usr/bin/env python3
"""Run a bounded Claude Code worker and normalize its result."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import platform
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Iterator


MODEL = "sonnet"
MAX_TASK_FILE_BYTES = 256 * 1024
MAX_QUICK_PROMPT_BYTES = 32 * 1024
LIMITS = {
    "medium": {"max_turns": 12, "timeout_seconds": 15 * 60},
    "high": {"max_turns": 24, "timeout_seconds": 30 * 60},
}
MUTATING_MODES = {"test", "edit"}
MIN_CLAUDE_VERSION = (2, 1, 205)
RESULT_FIELDS = {
    "status",
    "summary",
    "changed_files",
    "tests",
    "concerns",
    "recommended_next_action",
}


class PreflightError(RuntimeError):
    """Raised when a worker cannot be started safely."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delegate bounded repository work to Claude Code Sonnet."
    )
    parser.add_argument("--cwd", required=True, help="Path inside the target Git repository")
    task_input = parser.add_mutually_exclusive_group(required=True)
    task_input.add_argument("--task-file", help="UTF-8 Markdown strict task brief")
    task_input.add_argument(
        "--prompt",
        help="Concise quick-delegation goal; the launcher adds safe defaults",
    )
    parser.add_argument("--mode", required=True, choices=("review", "test", "edit"))
    parser.add_argument("--effort", required=True, choices=("medium", "high"))
    parser.add_argument(
        "--bash",
        choices=("never", "auto", "require"),
        default="auto",
        help=(
            "Worker Bash policy: never disables it, auto enables it only with the "
            "launcher's direct sandbox, and require makes it mandatory"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run preflight and print the planned invocation without calling Claude",
    )
    return parser.parse_args(argv)


def run_command(
    args: list[str], cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def resolve_git_root(cwd: Path) -> Path:
    if not cwd.is_dir():
        raise PreflightError(f"Working directory does not exist: {cwd}")
    try:
        result = run_command(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise PreflightError("--cwd must be inside a readable Git repository") from exc
    root = Path(result.stdout.strip()).resolve()
    if not root.is_dir():
        raise PreflightError("Git reported an invalid repository root")
    return root


def parse_version(text: str) -> tuple[int, int, int] | None:
    for token in text.replace("(", " ").replace(")", " ").split():
        pieces = token.split(".")
        if len(pieces) >= 3 and all(piece.isdigit() for piece in pieces[:3]):
            return tuple(int(piece) for piece in pieces[:3])  # type: ignore[return-value]
    return None


def resolve_claude_binary() -> str:
    override = os.environ.get("DELEGATE_TO_CLAUDE_BIN")
    binary = override or shutil.which("claude")
    if not binary:
        raise PreflightError("Claude Code CLI was not found on PATH")
    try:
        result = run_command([binary, "--version"])
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PreflightError("Claude Code CLI could not report its version") from exc
    if override and os.environ.get("DELEGATE_TO_CLAUDE_TESTING") == "1":
        return binary
    version = parse_version(result.stdout or result.stderr)
    if version is None or version < MIN_CLAUDE_VERSION:
        wanted = ".".join(str(part) for part in MIN_CLAUDE_VERSION)
        found = ".".join(str(part) for part in version) if version else "unknown"
        raise PreflightError(f"Claude Code >= {wanted} is required; found {found}")
    return binary


def validate_platform() -> None:
    system = platform.system()
    if system not in {"Darwin", "Linux"}:
        raise PreflightError(
            "Strict Claude Code sandboxing is supported here only on macOS or Linux"
        )


def load_task(path: Path) -> str:
    if not path.is_file():
        raise PreflightError(f"Task file does not exist: {path}")
    try:
        task = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PreflightError("Task file must be readable UTF-8 text") from exc
    if not task.strip():
        raise PreflightError("Task file must not be empty")
    if len(task.encode("utf-8")) > MAX_TASK_FILE_BYTES:
        raise PreflightError("Task file exceeds the 256 KiB safety limit")
    return task


def build_quick_task(prompt: str) -> str:
    if not prompt.strip():
        raise PreflightError("--prompt must not be empty")
    if len(prompt.encode("utf-8")) > MAX_QUICK_PROMPT_BYTES:
        raise PreflightError("--prompt exceeds the 32 KiB safety limit")
    return (
        "# Goal\n\n"
        f"{prompt.strip()}\n\n"
        "# Allowed scope\n\n"
        "Inspect only enough repository context to identify the minimal implementation. "
        "Change only files directly required by the goal and their focused tests.\n\n"
        "# Acceptance criteria\n\n"
        "Implement the requested behavior without unrelated cleanup or behavior changes. "
        "Keep the result compatible with the repository's existing conventions.\n\n"
        "# Required checks\n\n"
        "Identify and run the smallest relevant local test, lint, or type-check command when "
        "Bash is available. If Bash is unavailable, report the exact check the supervisor "
        "should run and do not claim it ran.\n\n"
        "# Existing user changes to preserve\n\n"
        "Preserve every pre-existing change from the Git baseline supplied by the launcher.\n\n"
        "# Forbidden actions\n\n"
        "Do not add or update dependencies unless the goal explicitly requires it. Do not "
        "access secrets or the network, invoke another agent, change Git state, commit, push, "
        "publish, deploy, or modify files outside the minimal scope. Stop as blocked if the "
        "goal is ambiguous or requires a forbidden action."
    )


def load_task_input(args: argparse.Namespace) -> tuple[str, str]:
    if args.prompt is not None:
        return build_quick_task(args.prompt), "quick"
    if args.task_file is None:  # argparse enforces this; keep a defensive check.
        raise PreflightError("One of --prompt or --task-file is required")
    return load_task(Path(args.task_file).expanduser().resolve()), "strict"


def git_status(root: Path) -> str:
    result = run_command(
        ["git", "status", "--short", "--untracked-files=all"], cwd=root
    )
    return result.stdout.rstrip()


def git_paths(root: Path) -> dict[str, str]:
    result = run_command(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=root,
    )
    tokens = result.stdout.split("\0")
    paths: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token:
            continue
        status = token[:2]
        path = token[3:]
        candidates = [path]
        if ("R" in status or "C" in status) and index < len(tokens):
            previous = tokens[index]
            index += 1
            if previous:
                candidates.append(previous)
        for candidate in candidates:
            paths[candidate] = file_fingerprint(root / candidate, status)
    return paths


def file_fingerprint(path: Path, status: str) -> str:
    digest = hashlib.sha256()
    digest.update(status.encode("utf-8", errors="replace"))
    try:
        if path.is_symlink():
            digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
        elif path.is_file():
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        else:
            digest.update(b"<missing-or-non-file>")
    except OSError as exc:
        digest.update(f"<unreadable:{exc.__class__.__name__}>".encode())
    return digest.hexdigest()


def changed_since(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(
        path
        for path in set(before) | set(after)
        if before.get(path) != after.get(path)
    )


def discover_project_secrets(root: Path) -> list[Path]:
    try:
        result = run_command(
            ["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=root
        )
    except subprocess.CalledProcessError:
        return []
    secrets: list[Path] = []
    exact_names = {
        "credentials.json",
        "service-account.json",
        "id_rsa",
        "id_ed25519",
    }
    for relative in result.stdout.split("\0"):
        if not relative:
            continue
        name = Path(relative).name.lower()
        if name == ".env" or name.startswith(".env.") or name in exact_names:
            secrets.append((root / relative).resolve())
    return sorted(set(secrets))


def absolute_permission_path(path: Path) -> str:
    return "//" + str(path.resolve()).lstrip("/")


def sensitive_environment_names() -> list[str]:
    markers = (
        "API_KEY",
        "AUTH",
        "COOKIE",
        "CREDENTIAL",
        "PASSWORD",
        "PRIVATE_KEY",
        "SECRET",
        "SESSION_TOKEN",
        "TOKEN",
    )
    return sorted(
        name
        for name in os.environ
        if any(marker in name.upper() for marker in markers)
    )


def credential_environment_names() -> list[str]:
    return sorted(
        {
            "ANTHROPIC_API_KEY",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "GITHUB_TOKEN",
            "GH_TOKEN",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "NPM_TOKEN",
            "OPENAI_API_KEY",
            *sensitive_environment_names(),
        }
    )


def build_settings(
    root: Path, session_env_dir: Path | None, use_inner_sandbox: bool
) -> dict[str, Any]:
    home = Path.home().resolve()
    project_secrets = discover_project_secrets(root)
    permission_denies = [
        "Agent",
        "WebFetch",
        "WebSearch",
        "Bash(claude *)",
        "Bash(codex *)",
        "Bash(git add *)",
        "Bash(git commit *)",
        "Bash(git push *)",
        "Bash(git clean *)",
        "Bash(git reset *)",
        "Bash(git restore *)",
        "Bash(git checkout *)",
        "Bash(git switch *)",
        "Bash(git stash *)",
        "Bash(git merge *)",
        "Bash(git rebase *)",
        "Bash(git cherry-pick *)",
        "Bash(gh *)",
        "Bash(curl *)",
        "Bash(wget *)",
        "Bash(ssh *)",
        "Bash(scp *)",
        "Bash(rsync *)",
        "Bash(kubectl *)",
        "Bash(terraform *)",
        "Bash(aws *)",
        "Bash(gcloud *)",
        "Bash(vercel *)",
        "Bash(netlify *)",
        "Bash(docker push *)",
        "Bash(npm publish *)",
        "Bash(cargo publish *)",
        "Bash(twine *)",
        "Bash(rm *)",
    ]
    for secret in project_secrets:
        permission_denies.append(f"Read({absolute_permission_path(secret)})")

    credential_files = [
        home / ".ssh",
        home / ".aws",
        home / ".config" / "gcloud",
        home / ".kube",
        home / ".git-credentials",
        *project_secrets,
    ]
    settings: dict[str, Any] = {
        "permissions": {
            "deny": permission_denies,
        }
    }
    if use_inner_sandbox:
        if session_env_dir is None:
            raise PreflightError("The strict worker sandbox requires an isolated session environment")
        settings["sandbox"] = {
            "enabled": True,
            "failIfUnavailable": True,
            "autoAllowBashIfSandboxed": True,
            "allowUnsandboxedCommands": False,
            "filesystem": {
                "denyRead": [str(path) for path in credential_files],
                "allowWrite": [str(session_env_dir)],
            },
            "network": {
                "deniedDomains": ["*"],
            },
            "credentials": {
                "files": [
                    {"path": str(path), "mode": "deny"}
                    for path in credential_files
                ],
                "envVars": [
                    {"name": name, "mode": "deny"}
                    for name in credential_environment_names()
                ],
            },
        }
    else:
        settings["sandbox"] = {"enabled": False}
    return settings


def build_boundary_prompt(mode: str, bash_enabled: bool) -> str:
    mode_rule = (
        "Do not edit, create, delete, or rename source files. Report findings only."
        if mode == "review"
        else "Edit only files required by the allowed scope and run only local checks."
    )
    rules = [
            "You are a subordinate coding worker. The calling model is the supervisor and retains final approval.",
            mode_rule,
            "Do not invoke Claude, Codex, subagents, skills, MCP tools, browsers, or other AI systems.",
            "Do not access the network, credentials, secret files, or environment tokens.",
            "Do not commit, stage, push, publish, deploy, change Git history, or alter repository configuration.",
            "Do not work outside the repository or retry outside the sandbox.",
            "Preserve all pre-existing user changes and avoid unrelated formatting or cleanup.",
            "If the task requires a forbidden action or is ambiguous, stop and return status blocked.",
            "Return the required structured result after completing the bounded task.",
    ]
    if bash_enabled:
        rules.append(
            "Bash is available only for the smallest relevant local checks. Never use it to access the network, secrets, credentials, Git mutation, or files outside the repository."
        )
    else:
        rules.append(
            "Bash is intentionally unavailable in this run. Do not claim that any command or test executed."
        )
    return "\n".join(rules)


def build_task_prompt(task: str, mode: str, baseline_status: str) -> str:
    baseline = baseline_status if baseline_status else "(clean working tree)"
    return (
        f"Execution mode: {mode}\n\n"
        "Authoritative task brief:\n"
        "---\n"
        f"{task.rstrip()}\n"
        "---\n\n"
        "Actual Git status captured immediately before this worker started:\n"
        "---\n"
        f"{baseline}\n"
        "---\n"
        "Treat the baseline as user-owned state. Do not overwrite unrelated changes."
    )


def build_claude_args(
    binary: str,
    mode: str,
    effort: str,
    settings_path: Path,
    schema: dict[str, Any],
    session_id: str,
    bash_enabled: bool,
) -> list[str]:
    if mode == "review":
        tools = allowed_tools = "Glob,Grep,Read"
    elif bash_enabled:
        tools = allowed_tools = "Bash,Edit,Glob,Grep,Read,Write"
    else:
        tools = allowed_tools = "Edit,Glob,Grep,Read,Write"
    permission_mode = "dontAsk" if mode == "review" else "acceptEdits"
    return [
        binary,
        "-p",
        "--model",
        MODEL,
        "--effort",
        effort,
        "--max-turns",
        str(LIMITS[effort]["max_turns"]),
        "--permission-mode",
        permission_mode,
        "--tools",
        tools,
        "--allowed-tools",
        allowed_tools,
        "--disable-slash-commands",
        "--safe-mode",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--no-chrome",
        "--no-session-persistence",
        "--session-id",
        session_id,
        "--settings",
        str(settings_path),
        "--append-system-prompt",
        build_boundary_prompt(mode, bash_enabled),
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(schema, separators=(",", ":")),
    ]


def invoke_worker(
    args: list[str],
    prompt: str,
    cwd: Path,
    timeout_seconds: float,
    env_file: Path,
) -> tuple[int, str, str, bool]:
    env = os.environ.copy()
    # Claude Code's built-in scrub currently conflicts with strict macOS Bash
    # sandboxing by attempting to create ~/.claude/session-env from inside the
    # sandbox. The generated sandbox credential list performs the child-process
    # scrub while preserving parent authentication.
    env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] = "0"
    env["CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR"] = "1"
    env["CLAUDE_ENV_FILE"] = str(env_file)
    process = subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=(os.name == "posix"),
    )
    try:
        stdout, stderr = process.communicate(prompt, timeout=timeout_seconds)
        return process.returncode, stdout, stderr, False
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            stdout, stderr = process.communicate()
        return 124, stdout, stderr, True


def load_schema(skill_dir: Path) -> dict[str, Any]:
    schema_path = skill_dir / "references" / "worker-result.schema.json"
    try:
        return json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreflightError("Bundled worker result schema is missing or invalid") from exc


def failure_result(summary: str, concern: str, changed_files: list[str]) -> dict[str, Any]:
    return {
        "status": "failed",
        "summary": summary,
        "changed_files": changed_files,
        "tests": [],
        "concerns": [concern],
        "recommended_next_action": "The supervisor should inspect the diff and take the task back or re-scope it once.",
    }


def classify_failure(stderr: str, timed_out: bool) -> str:
    if timed_out:
        return "Worker timed out; do not repeat the same delegation unchanged."
    lowered = stderr.lower()
    if "rate limit" in lowered or "rate_limit" in lowered or "429" in lowered:
        return "Claude Code reported a rate limit; do not retry automatically."
    if "auth" in lowered or "login" in lowered or "credential" in lowered:
        return "Claude Code authentication failed; do not retry automatically."
    if "sandbox" in lowered:
        return "The required Claude Code sandbox failed; do not run without it or retry automatically."
    return "Claude Code exited unsuccessfully; inspect the terminal error without accepting its work."


def parse_worker_output(stdout: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Claude Code returned malformed JSON") from exc
    if not isinstance(envelope, dict):
        raise ValueError("Claude Code JSON envelope was not an object")
    structured = envelope.get("structured_output")
    if not isinstance(structured, dict):
        raise ValueError("Claude Code response did not include structured_output")
    if set(structured) != RESULT_FIELDS:
        raise ValueError("Claude Code structured output had unexpected fields")
    if structured.get("status") not in {"completed", "blocked", "failed"}:
        raise ValueError("Claude Code structured output had an invalid status")
    for field in ("changed_files", "tests", "concerns"):
        if not isinstance(structured.get(field), list):
            raise ValueError(f"Claude Code structured output had an invalid {field}")
    for field in ("summary", "recommended_next_action"):
        if not isinstance(structured.get(field), str):
            raise ValueError(f"Claude Code structured output had an invalid {field}")
    return structured, envelope


def cache_root() -> Path:
    override = os.environ.get("DELEGATE_TO_CLAUDE_CACHE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg).expanduser().resolve() / "delegate-to-claude"
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Caches" / "delegate-to-claude"
    return Path.home() / ".cache" / "delegate-to-claude"


def filtered_usage(envelope: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "total_cost_usd",
        "duration_ms",
        "duration_api_ms",
        "num_turns",
        "usage",
        "modelUsage",
    )
    return {key: envelope[key] for key in allowed if key in envelope}


def write_receipt(
    *,
    task: str,
    input_style: str,
    bash_requested: str,
    bash_enabled: bool,
    sandbox_source: str,
    mode: str,
    effort: str,
    duration_seconds: float,
    result: dict[str, Any],
    envelope: dict[str, Any],
) -> None:
    tests = []
    for test in result.get("tests", []):
        if isinstance(test, dict):
            tests.append(
                {
                    "command": str(test.get("command", "")),
                    "outcome": str(test.get("outcome", "")),
                }
            )
    receipt = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "task_id": hashlib.sha256(task.encode("utf-8")).hexdigest()[:16],
        "input_style": input_style,
        "bash_requested": bash_requested,
        "bash_enabled": bash_enabled,
        "sandbox_source": sandbox_source,
        "model": MODEL,
        "effort": effort,
        "mode": mode,
        "duration_seconds": round(duration_seconds, 3),
        "status": result.get("status", "failed"),
        "changed_files": list(result.get("changed_files", [])),
        "tests": tests,
        "usage": filtered_usage(envelope),
    }
    try:
        directory = cache_root()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "runs.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    except OSError as exc:
        print(f"warning: could not write execution receipt: {exc}", file=sys.stderr)


@contextlib.contextmanager
def mutation_lock(root: Path, mode: str) -> Iterator[None]:
    if mode not in MUTATING_MODES:
        yield
        return
    key = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:20]
    lock_path = Path(tempfile.gettempdir()) / f"delegate-to-claude-{key}.lock"
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PreflightError(
                "Another test/edit worker is active for this repository; wait for it to finish"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def session_env_root() -> Path:
    override = os.environ.get("DELEGATE_TO_CLAUDE_SESSION_ENV_ROOT")
    if override and os.environ.get("DELEGATE_TO_CLAUDE_TESTING") == "1":
        return Path(override).expanduser().resolve()
    return Path.home() / ".claude" / "session-env"


def outer_sandbox_source() -> str | None:
    if os.environ.get("CODEX_SANDBOX"):
        return "outer-codex"
    if os.environ.get("CLAUDECODE") == "1" or os.environ.get(
        "CLAUDE_CODE_CHILD_SESSION"
    ):
        return "outer-claude"
    return None


def resolve_bash_access(
    policy: str, mode: str, detected_outer_source: str | None
) -> tuple[bool, str]:
    sandbox_source = detected_outer_source or "claude-code"
    if mode == "review":
        if policy == "require":
            raise PreflightError("--bash require is incompatible with review mode")
        return False, sandbox_source
    if policy == "never":
        return False, sandbox_source
    if detected_outer_source is None:
        return True, sandbox_source
    if policy == "require":
        return True, "claude-code"
    return False, sandbox_source


def environment_file_content(bash_enabled: bool, nested_supervisor: bool) -> str:
    if not (bash_enabled and nested_supervisor):
        return ""
    return "".join(
        f"unset {shlex.quote(name)}\n" for name in credential_environment_names()
    )


@contextlib.contextmanager
def isolated_session_env(session_id: str) -> Iterator[Path]:
    root = session_env_root()
    directory = root / session_id
    try:
        root.mkdir(parents=True, exist_ok=True)
        directory.mkdir(mode=0o700)
    except OSError as exc:
        with contextlib.suppress(OSError):
            if directory.is_dir() and directory.parent == root:
                shutil.rmtree(directory)
        raise PreflightError(
            "The strict Bash sandbox requires a writable UUID-scoped directory under "
            f"{root}; permit the launcher to create it or disable worker Bash"
        ) from exc
    try:
        yield directory
    finally:
        if directory.is_symlink():
            directory.unlink(missing_ok=True)
        elif directory.parent == root and directory.name == session_id:
            shutil.rmtree(directory, ignore_errors=True)


def print_result(result: dict[str, Any]) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.monotonic()
    skill_dir = Path(__file__).resolve().parent.parent
    envelope: dict[str, Any] = {}
    before: dict[str, str] = {}
    root: Path | None = None
    task = ""
    input_style = "strict"
    bash_enabled = False
    sandbox_source = "unknown"
    try:
        validate_platform()
        root = resolve_git_root(Path(args.cwd).expanduser().resolve())
        task, input_style = load_task_input(args)
        binary = resolve_claude_binary()
        schema = load_schema(skill_dir)
        baseline_status = git_status(root)
        before = git_paths(root)
        timeout_seconds = float(LIMITS[args.effort]["timeout_seconds"])
        detected_outer_source = outer_sandbox_source()
        nested_supervisor = detected_outer_source is not None
        bash_enabled, sandbox_source = resolve_bash_access(
            args.bash, args.mode, detected_outer_source
        )
        use_inner_sandbox = not nested_supervisor or bash_enabled
        if os.environ.get("DELEGATE_TO_CLAUDE_TESTING") == "1":
            timeout_seconds = float(
                os.environ.get("DELEGATE_TO_CLAUDE_TIMEOUT_SECONDS", timeout_seconds)
            )

        with mutation_lock(root, args.mode):
            session_id = str(uuid.uuid4())
            session_context = (
                contextlib.nullcontext(None)
                if not use_inner_sandbox
                else isolated_session_env(session_id)
            )
            with tempfile.TemporaryDirectory(prefix="delegate-to-claude-") as temp_dir, session_context as session_env_dir:
                settings_path = Path(temp_dir) / "settings.json"
                env_file = Path(temp_dir) / "environment.sh"
                settings_path.write_text(
                    json.dumps(
                        build_settings(root, session_env_dir, use_inner_sandbox),
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                env_file.write_text(
                    environment_file_content(bash_enabled, nested_supervisor),
                    encoding="utf-8",
                )
                claude_args = build_claude_args(
                    binary,
                    args.mode,
                    args.effort,
                    settings_path,
                    schema,
                    session_id,
                    bash_enabled,
                )
                if args.dry_run:
                    print_result(
                        {
                            "status": "completed",
                            "summary": (
                                f"Preflight passed for {input_style} delegation; would run "
                                f"{MODEL} with {args.effort} effort, "
                                f"{LIMITS[args.effort]['max_turns']} turns, and "
                                f"Bash {'enabled' if bash_enabled else 'disabled'} "
                                f"under {sandbox_source}."
                            ),
                            "changed_files": [],
                            "tests": [],
                            "concerns": [],
                            "recommended_next_action": "Run again without --dry-run when ready.",
                        }
                    )
                    return 0
                return_code, stdout, stderr, timed_out = invoke_worker(
                    claude_args,
                    build_task_prompt(task, args.mode, baseline_status),
                    root,
                    timeout_seconds,
                    env_file,
                )

        after = git_paths(root)
        actual_changes = changed_since(before, after)
        if return_code != 0 or timed_out:
            result = failure_result(
                "The delegated worker did not complete successfully.",
                classify_failure(stderr, timed_out),
                actual_changes,
            )
            write_receipt(
                task=task,
                input_style=input_style,
                bash_requested=args.bash,
                bash_enabled=bash_enabled,
                sandbox_source=sandbox_source,
                mode=args.mode,
                effort=args.effort,
                duration_seconds=time.monotonic() - started,
                result=result,
                envelope=envelope,
            )
            print_result(result)
            if stderr.strip():
                print(stderr.rstrip(), file=sys.stderr)
            return 124 if timed_out else 3

        try:
            result, envelope = parse_worker_output(stdout)
        except ValueError as exc:
            result = failure_result(
                "The delegated worker returned an invalid structured result.",
                str(exc),
                actual_changes,
            )
            write_receipt(
                task=task,
                input_style=input_style,
                bash_requested=args.bash,
                bash_enabled=bash_enabled,
                sandbox_source=sandbox_source,
                mode=args.mode,
                effort=args.effort,
                duration_seconds=time.monotonic() - started,
                result=result,
                envelope=envelope,
            )
            print_result(result)
            return 3

        reported = sorted(set(str(path) for path in result["changed_files"]))
        result["changed_files"] = actual_changes
        if reported != actual_changes:
            result["concerns"].append(
                "Worker-reported changed files differed from the Git baseline; the launcher used the measured list."
            )
        if args.mode == "review" and actual_changes:
            result["status"] = "failed"
            result["concerns"].append(
                "Review mode changed repository files; reject the result and inspect the diff."
            )
        if not bash_enabled:
            for test in result["tests"]:
                if isinstance(test, dict):
                    test["outcome"] = "not_run"
                    test["details"] = (
                        "Bash is disabled for this worker; "
                        "the supervisor must run this check independently."
                    )
            if args.mode in MUTATING_MODES:
                result["concerns"].append(
                    f"Bash was disabled by --bash {args.bash} under {sandbox_source}; "
                    "the supervisor must run required checks independently."
                )
        elif nested_supervisor:
            result["concerns"].append(
                "Nested Bash was enabled by --bash require inside the launcher's strict "
                "Claude Code sandbox; the supervisor must still review and rerun the check."
            )
        write_receipt(
            task=task,
            input_style=input_style,
            bash_requested=args.bash,
            bash_enabled=bash_enabled,
            sandbox_source=sandbox_source,
            mode=args.mode,
            effort=args.effort,
            duration_seconds=time.monotonic() - started,
            result=result,
            envelope=envelope,
        )
        print_result(result)
        return 0 if result["status"] == "completed" else 3
    except PreflightError as exc:
        actual_changes: list[str] = []
        if root is not None and before:
            with contextlib.suppress(Exception):
                actual_changes = changed_since(before, git_paths(root))
        result = failure_result("Worker preflight failed.", str(exc), actual_changes)
        print_result(result)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
