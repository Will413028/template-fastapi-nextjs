"""Every contract in .importlinter fails when its rule is broken.

A contract whose module pattern matches nothing, or whose rule cannot fire, stays green forever. Each case copies the
source tree, adds one import that breaks exactly one rule, and asserts import-linter reports that contract broken.
A new contract without a case here fails test_every_contract_has_a_case.
"""

import configparser
import logging
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from importlinter.cli import lint_imports

if TYPE_CHECKING:
    from collections.abc import Iterator

BACKEND = Path(__file__).resolve().parents[1]
CONFIG = BACKEND / ".importlinter"

# contract id -> (file under src/app to append to, the import that breaks it)
CASES: dict[str, tuple[str, str]] = {
    "top-layers": ("core/errors.py", "import app.http"),
    "modules-independent": ("modules/items/service.py", "import app.modules.auth.service"),
    "module-layers": ("modules/items/models.py", "import app.modules.items.service"),
    "no-cycles": ("core/errors.py", "import app.cli"),
    "http-stays-at-the-edge": ("modules/items/service.py", "import fastapi"),
    "sql-below-the-router": ("modules/items/router.py", "import sqlalchemy"),
    "light-healthcheck": ("cli/healthcheck.py", "import pydantic"),
}


def _contracts() -> dict[str, str]:
    parser = configparser.ConfigParser()
    parser.read(CONFIG)
    prefix = "importlinter:contract:"
    return {s.removeprefix(prefix): parser[s]["name"] for s in parser.sections() if s.startswith(prefix)}


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A copy of src/app that import-linter reads instead of the installed package.

    Finding the copy's modules imports its packages, so every app module is put back exactly as it was afterwards;
    a copy left in sys.modules breaks later tests that patch the real modules.
    """
    shutil.copytree(BACKEND / "src" / "app", tmp_path / "app", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(sys, "path", [str(tmp_path), *sys.path])
    saved = {name: module for name, module in sys.modules.items() if _is_app(name)}
    for name in saved:
        del sys.modules[name]
    yield tmp_path / "app"
    for name in [name for name in sys.modules if _is_app(name)]:
        del sys.modules[name]
    sys.modules.update(saved)


def _is_app(name: str) -> bool:
    return name == "app" or name.startswith("app.")


def _lint(capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    # lint_imports configures logging with disable_existing_loggers, which silences every logger already created
    # (the app's included) for the rest of the run; put each logger's flag back.
    loggers = [lg for lg in logging.Logger.manager.loggerDict.values() if isinstance(lg, logging.Logger)]
    disabled = [lg.disabled for lg in loggers]
    try:
        code = lint_imports(config_filename=str(CONFIG), no_cache=True, no_logo=True)
    finally:
        for lg, flag in zip(loggers, disabled, strict=True):
            lg.disabled = flag
    return code, capsys.readouterr().out


def test_every_contract_has_a_case() -> None:
    assert set(_contracts()) == set(CASES)


def test_unchanged_tree_keeps_every_contract(tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert tree.is_dir()
    code, out = _lint(capsys)
    assert code == 0, out


@pytest.mark.parametrize("contract", sorted(CASES))
def test_contract_fails_on_its_violation(tree: Path, capsys: pytest.CaptureFixture[str], contract: str) -> None:
    target, line = CASES[contract]
    path = tree / target
    path.write_text(path.read_text() + f"\n{line}\n")

    code, out = _lint(capsys)

    name = _contracts()[contract]
    assert code != 0
    assert any(row.startswith(name) and row.rstrip().endswith("BROKEN") for row in out.splitlines()), out
