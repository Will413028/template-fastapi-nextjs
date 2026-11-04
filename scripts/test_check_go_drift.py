"""Tests for check_go_drift.py against a stand-in Go repository. Run: python3 -m unittest discover -s scripts -p 'test_*.py'"""

import importlib
import io
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
check_go_drift = importlib.import_module("check_go_drift")
ROOT = check_go_drift.ROOT


def git(cwd: pathlib.Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


class CheckGoDriftTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.go = pathlib.Path(tmp.name)
        # a Go repository whose shared files equal this repository's
        for rel in check_go_drift.our_files():
            if check_go_drift.shared(rel) or rel in check_go_drift.EXCEPTIONS:
                dst = self.go / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / rel, dst)
                if rel in check_go_drift.EXCEPTIONS:  # those differ in Go on purpose
                    dst.write_text(dst.read_text(encoding="utf-8") + "\n// Go\n", encoding="utf-8")
        git(self.go, "init", "-q")

    def run_check(self) -> tuple[int, str]:
        git(self.go, "add", "-A")
        git(self.go, "commit", "-qm", "go", "--allow-empty")
        out = io.StringIO()
        argv = ["check_go_drift.py", "--go-repo", str(self.go), "--ref", "HEAD"]
        with redirect_stdout(out), mock.patch.object(sys, "argv", argv):
            code = check_go_drift.main()
        return code, out.getvalue()

    def edit(self, rel: str, change) -> None:
        path = self.go / rel
        old = path.read_text(encoding="utf-8")
        new = change(old)
        self.assertNotEqual(new, old, f"the edit did not change {rel}")
        path.write_text(new, encoding="utf-8")

    def test_clean_when_the_shared_files_match(self) -> None:
        code, out = self.run_check()
        self.assertEqual((code, out), (0, f"No drift from template-go-nextjs@{self.head()}.\n"))

    def test_reports_a_changed_shared_file(self) -> None:
        self.edit("frontend/src/lib/api/errors.ts", lambda t: t + "// new in Go\n")
        code, out = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("differs: frontend/src/lib/api/errors.ts", out)

    def test_reports_files_on_one_side_of_a_shared_directory(self) -> None:
        (self.go / "frontend/src/new.ts").write_text("export {};\n", encoding="utf-8")
        (self.go / "frontend/biome.json").unlink()
        code, out = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("only in Go: frontend/src/new.ts", out)
        self.assertIn("only here: frontend/biome.json", out)

    def test_brand_names_and_exceptions_are_not_drift(self) -> None:
        self.edit("backend/api/openapi.yaml", lambda t: t.replace("template-fastapi-nextjs", "template-go-nextjs"))
        self.edit("frontend/next.config.ts", lambda t: t + "// Caddy in Go\n")
        self.assertEqual(self.run_check()[0], 0)

    def test_reports_an_exception_that_no_longer_differs(self) -> None:
        self.edit("frontend/vercel.json", lambda t: t + "\n")
        (self.go / "frontend/vercel.json").write_text((ROOT / "frontend/vercel.json").read_text(encoding="utf-8"))
        code, out = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("exception no longer differs from Go; remove it: frontend/vercel.json", out)

    def test_files_outside_the_shared_set_are_ignored(self) -> None:
        (self.go / "backend/go.mod").write_text("module x\n", encoding="utf-8")
        self.assertEqual(self.run_check()[0], 0)

    def head(self) -> str:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=self.go, check=True, capture_output=True, text=True
        ).stdout.strip()


if __name__ == "__main__":
    unittest.main()
