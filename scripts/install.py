#!/usr/bin/env python3
"""Install the shared skill into Codex and/or Claude Code with symlinks."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


TARGETS = {
    "codex": (
        ("codex", Path(".agents/skills/delegate-to-claude")),
        ("codex-legacy", Path(".codex/skills/delegate-to-claude")),
    ),
    "claude": (("claude", Path(".claude/skills/delegate-to-claude")),),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Symlink delegate-to-claude into personal agent skill directories."
    )
    parser.add_argument(
        "--target",
        choices=("both", "codex", "claude"),
        default="both",
        help="Agent installation target (default: both)",
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=Path.home(),
        help="Home directory to install under (default: current user's home)",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def selected_targets(name: str) -> list[str]:
    return ["codex", "claude"] if name == "both" else [name]


def same_target(link: Path, source: Path) -> bool:
    if not link.is_symlink():
        return False
    try:
        return link.resolve(strict=True) == source.resolve(strict=True)
    except OSError:
        return False


def main() -> int:
    args = parse_args()
    repository = Path(__file__).resolve().parent.parent
    source = repository / "skill" / "delegate-to-claude"
    if not (source / "SKILL.md").is_file():
        print(f"error: skill source is missing: {source}", file=sys.stderr)
        return 2

    home = args.home.expanduser().resolve()
    plans: list[tuple[str, Path, str]] = []
    conflicts: list[Path] = []
    for name in selected_targets(args.target):
        for label, relative_destination in TARGETS[name]:
            destination = home / relative_destination
            if same_target(destination, source):
                plans.append((label, destination, "already installed"))
            elif destination.exists() or destination.is_symlink():
                conflicts.append(destination)
            else:
                plans.append((label, destination, "create"))

    if conflicts:
        print("error: refusing to overwrite existing skill paths:", file=sys.stderr)
        for conflict in conflicts:
            print(f"  {conflict}", file=sys.stderr)
        print("Move or remove the conflicting paths yourself, then run again.", file=sys.stderr)
        return 3

    if args.dry_run:
        for name, destination, action in plans:
            print(f"{name}: {action}: {destination} -> {source}")
        return 0

    created: list[Path] = []
    try:
        for name, destination, action in plans:
            if action == "already installed":
                print(f"{name}: already installed at {destination}")
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(source, destination, target_is_directory=True)
            created.append(destination)
            print(f"{name}: installed {destination} -> {source}")
    except OSError as exc:
        for destination in reversed(created):
            try:
                destination.unlink()
            except OSError:
                pass
        print(f"error: installation failed and new links were rolled back: {exc}", file=sys.stderr)
        return 4


    if args.target in {"both", "codex"}:
        print("Restart Codex to discover a newly installed skill.")
    if args.target in {"both", "claude"}:
        print("Restart Claude Code if its personal skills directory was not present at startup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
