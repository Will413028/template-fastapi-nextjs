"""Tests for check_vercel_config.py. Run: python3 -m unittest discover -s scripts -p 'test_*.py'"""

import copy
import importlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
check_vercel_config = importlib.import_module("check_vercel_config")

REAL = SCRIPTS.parent / "frontend" / "vercel.json"
# low entropy on purpose, so secret scanners do not take this fake for a real key
FAKE_KEY = "AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH"


def real_config() -> dict:
    return json.loads(REAL.read_text(encoding="utf-8"))


def api_route(config: dict) -> dict:
    return next(r for r in config["routes"] if r["src"] == "/api/(.*)")


class CheckVercelConfigTest(unittest.TestCase):
    def assert_fails(self, config: dict, fragment: str) -> None:
        found = check_vercel_config.problems(config)
        self.assertTrue(any(fragment in p for p in found), f"{fragment!r} not in {found}")

    def test_the_repository_file_passes(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "check_vercel_config.py"), str(REAL)], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_braced_references_pass(self):
        config = real_config()
        api_route(config)["transforms"][0]["args"] = "${ORIGIN_SECRET}"
        self.assertEqual(check_vercel_config.problems(config), [])

    def test_missing_transform(self):
        config = real_config()
        del api_route(config)["transforms"]
        self.assert_fails(config, "found 0")

    def test_route_env_missing(self):
        config = real_config()
        del api_route(config)["env"]
        self.assert_fails(config, "env must list BACKEND_ORIGIN")

    def test_env_must_be_a_list_of_names(self):
        config = real_config()
        api_route(config)["env"] = "BACKEND_ORIGIN"
        api_route(config)["transforms"][0]["env"] = "ORIGIN_SECRET"
        self.assert_fails(config, "env must list BACKEND_ORIGIN")
        self.assert_fails(config, "env must list ORIGIN_SECRET")

    def test_a_malformed_transform_is_a_problem_not_a_crash(self):
        config = real_config()
        api_route(config)["transforms"] = ["X-Origin-Secret"]
        self.assert_fails(config, "found 0")

    def test_transform_env_missing(self):
        config = real_config()
        api_route(config)["transforms"][0]["env"] = []
        self.assert_fails(config, "env must list ORIGIN_SECRET")

    def test_args_must_be_only_the_reference(self):
        config = real_config()
        api_route(config)["transforms"][0]["args"] = "prefix-$ORIGIN_SECRET"
        self.assert_fails(config, "must be exactly $ORIGIN_SECRET")

    def test_a_literal_secret_fails_twice(self):
        config = real_config()
        api_route(config)["transforms"][0]["args"] = FAKE_KEY
        self.assert_fails(config, "must be exactly $ORIGIN_SECRET")
        self.assert_fails(config, "looks like a secret")

    def test_a_secret_anywhere_fails(self):
        config = real_config()
        config["headers"] = [{"source": "/(.*)", "headers": [{"key": "X-Debug", "value": FAKE_KEY}]}]
        found = check_vercel_config.problems(config)
        self.assertEqual(len(found), 1, found)
        self.assertNotIn(FAKE_KEY, found[0], "the message must not repeat the secret")

    def test_op_must_be_set(self):
        config = real_config()
        api_route(config)["transforms"][0]["op"] = "append"
        self.assert_fails(config, "op set")

    def test_dest_must_point_at_the_backend_api(self):
        for dest in ("https://api.example.com/api/$1", "${BACKEND_ORIGIN}/$1", "${OTHER}/api/$1"):
            config = real_config()
            api_route(config)["dest"] = dest
            self.assert_fails(config, "dest must be")

    def test_exactly_one_api_route(self):
        config = real_config()
        config["routes"].append(copy.deepcopy(api_route(config)))
        self.assert_fails(config, "found 2")
        self.assert_fails({"routes": []}, "found 0")

    def test_unreadable_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{not json")
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "check_vercel_config.py"), f.name], capture_output=True, text=True
        )
        pathlib.Path(f.name).unlink()
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
