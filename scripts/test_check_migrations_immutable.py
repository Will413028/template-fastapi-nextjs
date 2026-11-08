"""Tests for check_migrations_immutable.py against throwaway git repositories. Run: python3 -m unittest discover scripts"""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parent / "check_migrations_immutable.py"
VERSIONS = "backend/src/app/migrations/versions"


class MigrationsImmutableTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env |= {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "test")
        (self.repo / "scripts").mkdir()
        shutil.copy(SCRIPT, self.repo / "scripts" / SCRIPT.name)
        self.write(f"{VERSIONS}/r1_baseline.py", "revision = 'baseline'\n")
        self.commit("feat: baseline")
        self.git("checkout", "-q", "-b", "dev")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env, check=True, capture_output=True, text=True
        ).stdout

    def write(self, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def check(self, env=None):
        return subprocess.run(
            [sys.executable, str(self.repo / "scripts" / SCRIPT.name), "main"],
            cwd=self.repo, env=env or self.env, capture_output=True, text=True,
        )

    def test_adding_a_revision_passes(self):
        self.write(f"{VERSIONS}/r2_widgets.py", "revision = 'widgets'\n")
        self.commit("feat: widgets")
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_branch_revision_may_still_change(self):
        self.write(f"{VERSIONS}/r2_widgets.py", "revision = 'widgets'\n")
        self.commit("feat: widgets")
        self.write(f"{VERSIONS}/r2_widgets.py", "revision = 'widgets'  # renamed column\n")
        self.assertEqual(self.check().returncode, 0)

    def test_editing_a_base_revision_fails_even_uncommitted(self):
        self.write(f"{VERSIONS}/r1_baseline.py", "revision = 'baseline'\nextra = 1\n")
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("r1_baseline.py", result.stdout)

    def test_deleting_or_renaming_a_base_revision_fails(self):
        self.git("mv", f"{VERSIONS}/r1_baseline.py", f"{VERSIONS}/r1_start.py")
        self.commit("refactor: rename")
        self.assertEqual(self.check().returncode, 1)

    def test_it_finds_the_repository_when_a_hook_exports_git_dir(self):
        self.write(f"{VERSIONS}/r1_baseline.py", "changed\n")
        env = self.env | {"GIT_DIR": str(self.repo / ".git")}
        result = subprocess.run(
            [sys.executable, str(self.repo / "scripts" / SCRIPT.name), "main"],
            cwd=self.repo / "scripts", env=env, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1, result.stderr)


if __name__ == "__main__":
    unittest.main()
