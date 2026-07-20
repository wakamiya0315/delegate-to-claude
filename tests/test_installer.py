from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "scripts" / "install.py"
SOURCE = ROOT / "skill" / "delegate-to-claude"


class InstallerTests(unittest.TestCase):
    def run_installer(self, home: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(INSTALLER), "--home", str(home), *extra],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_installs_both_symlinks_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            first = self.run_installer(home)
            self.assertEqual(first.returncode, 0, first.stderr)
            for parent in (".codex", ".claude"):
                link = home / parent / "skills" / "delegate-to-claude"
                self.assertTrue(link.is_symlink())
                self.assertEqual(link.resolve(), SOURCE.resolve())

            second = self.run_installer(home)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("already installed", second.stdout)

    def test_conflict_preflight_prevents_partial_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            conflict = home / ".claude" / "skills" / "delegate-to-claude"
            conflict.parent.mkdir(parents=True)
            conflict.write_text("user-owned", encoding="utf-8")

            result = self.run_installer(home)
            self.assertEqual(result.returncode, 3)
            codex_link = home / ".codex" / "skills" / "delegate-to-claude"
            self.assertFalse(codex_link.exists())
            self.assertEqual(conflict.read_text(encoding="utf-8"), "user-owned")

    def test_dry_run_does_not_create_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            result = self.run_installer(home, "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((home / ".codex").exists())
            self.assertFalse((home / ".claude").exists())


if __name__ == "__main__":
    unittest.main()
