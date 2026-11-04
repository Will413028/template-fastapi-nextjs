"""Tests for rename.py on a copy of the tracked files. Run: python3 -m unittest discover -s scripts -p 'test_*.py'"""

import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "rename.py"


def git(cwd: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


# a derived project has run rename.py already: there is nothing left to rename
@unittest.skipUnless(
    "template-fastapi-nextjs" in (ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"), "already renamed"
)
class RenameTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.copy = pathlib.Path(tmp.name)
        # the working tree's tracked files, so uncommitted edits are tested too
        for rel in git(ROOT, "ls-files", "-z").split("\0"):
            src = ROOT / rel
            if rel and src.is_file():
                dst = self.copy / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        git(self.copy, "init", "-q")
        git(self.copy, "add", "-A")

    def rename(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args], cwd=self.copy, capture_output=True, text=True, check=False
        )

    def read(self, rel: str) -> str:
        return (self.copy / rel).read_text(encoding="utf-8")

    def test_renames_every_placeholder(self) -> None:
        result = self.rename("ci-probe")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("POSTGRES_DB=ci_probe\n", self.read(".env-example"))
        self.assertIn("DB_APP_USER=ci_probe_app\n", self.read(".env-example"))
        self.assertIn('OWNER_ROLE = "ci_probe_owner"', self.read("backend/tests/dbtest.py"))
        self.assertIn("image: ci-probe-backend", self.read("docker-compose.yml"))
        self.assertIn('name = "ci-probe-backend"', self.read("backend/pyproject.toml"))
        self.assertIn('name = "ci-probe-backend"', self.read("backend/uv.lock"))
        self.assertIn('"name": "ci-probe-frontend"', self.read("frontend/package.json"))
        self.assertIn("github.com/Will413028/ci-probe/security", self.read("SECURITY.md"))

    def test_repo_option(self) -> None:
        result = self.rename("ci-probe", "--repo", "acme/shop")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("github.com/acme/shop/security", self.read("SECURITY.md"))

    def test_fails_on_an_unknown_file_that_names_the_placeholder(self) -> None:
        (self.copy / "new.md").write_text("db: template_fastapi_nextjs\n", encoding="utf-8")
        git(self.copy, "add", "new.md")
        result = self.rename("ci-probe")
        self.assertEqual(result.returncode, 1)
        self.assertIn("new.md:1", result.stderr)
        # nothing was changed, so the run can be repeated once the file is listed
        self.assertEqual(git(self.copy, "diff", "--name-only"), "")

    def test_refuses_a_renamed_clone_and_a_bad_name(self) -> None:
        self.assertEqual(self.rename("ci-probe").returncode, 0)
        self.assertIn("already renamed", self.rename("other").stderr)
        self.assertIn("invalid name", self.rename("Bad_Name").stderr)

    def test_refuses_names_postgresql_cannot_use(self) -> None:
        for name in ("user", "order", "a" * 58, "template-fastapi-nextjs-shop"):
            with self.subTest(name=name):
                result = self.rename(name)
                self.assertEqual(result.returncode, 2)
                self.assertIn("invalid name", result.stderr)
        self.assertEqual(self.rename("a" * 57).returncode, 0)


if __name__ == "__main__":
    unittest.main()
