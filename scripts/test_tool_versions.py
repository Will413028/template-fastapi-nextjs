"""Offline tests for tool_versions.py. Run: python3 -m unittest discover -s scripts -p 'test_*.py'"""

import importlib
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
tv = importlib.import_module("tool_versions")

A, B, C = ("a" * 64), ("b" * 64), ("c" * 64)

MAKEFILE = f"""HADOLINT_VERSION := 2.15.1
HADOLINT_SHA256_macos-arm64  := {A}
HADOLINT_SHA256_linux-x86_64 := {B}
HADOLINT_SHA256_linux-arm64  := {C}
"""


class MakeVars(unittest.TestCase):
    def test_set_and_read(self):
        text = tv.set_make_var(MAKEFILE, "HADOLINT_VERSION", "2.16.0")
        self.assertEqual(tv.make_var(text, "HADOLINT_VERSION"), "2.16.0")
        self.assertEqual(tv.make_var(text, "HADOLINT_SHA256_linux-arm64"), C)

    def test_checksum_lines(self):
        sums = tv.checksum_lines(f"{A}  hadolint-linux-x86_64\n{B} *hadolint-linux-arm64\nnoise\n")
        self.assertEqual(sums, {"hadolint-linux-x86_64": A, "hadolint-linux-arm64": B})


class Verify(unittest.TestCase):
    def test_complete_pins_pass(self):
        self.assertEqual(tv.pin_problems("hadolint", MAKEFILE), [])

    def test_missing_platform_fails(self):
        text = "\n".join(line for line in MAKEFILE.splitlines() if "linux-arm64" not in line)
        self.assertEqual(tv.pin_problems("hadolint", text), ["hadolint: HADOLINT_SHA256_linux-arm64 is missing or not a sha256"])

    def test_bad_checksum_fails(self):
        problems = tv.pin_problems("hadolint", MAKEFILE.replace(B, "not-a-sum"))
        self.assertEqual(problems, ["hadolint: HADOLINT_SHA256_linux-x86_64 is missing or not a sha256"])

    def test_version_with_prefix_fails(self):
        problems = tv.pin_problems("hadolint", MAKEFILE.replace("2.15.1", "v2.15.1"))
        self.assertEqual(problems, ["hadolint: HADOLINT_VERSION is not a plain x.y.z version"])


if __name__ == "__main__":
    unittest.main()
