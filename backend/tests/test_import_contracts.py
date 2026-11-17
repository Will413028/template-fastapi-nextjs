"""Every contract in .importlinter fails when its rule is broken.

A contract whose module pattern matches nothing, or whose rule cannot fire, stays green forever. Each case copies the
source tree, adds one import that breaks the rule, and lints only that contract, so the exit code shows that this
contract fired and not a neighbour. A new contract without a case here fails test_every_contract_has_a_case.
"""

import configparser
import logging
import logging.config
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from importlinter.cli import lint_imports

if TYPE_CHECKING:
    from collections.abc import Iterator

BACKEND = Path(__file__).resolve().parents[1]
CONFIG = BACKEND / ".importlinter"

# contract id -> (file under src/app to append to, the import that breaks it). The cases use the items and auth
# modules; a project that removes them points the cases at its own modules.
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
    # lint_imports puts the working directory first on sys.path: run it from the copy's parent so the copy wins
    # wherever pytest was started, and give sys.path back afterwards.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", [str(tmp_path), *sys.path])
    # lint_imports calls logging.config.dictConfig, which disables every existing logger and closes their handlers.
    monkeypatch.setattr(logging.config, "dictConfig", _no_logging_config)
    saved = {name: module for name, module in sys.modules.items() if _is_app(name)}
    for name in saved:
        del sys.modules[name]
    yield tmp_path / "app"
    for name in [name for name in sys.modules if _is_app(name)]:
        del sys.modules[name]
    sys.modules.update(saved)


def _no_logging_config(_config: dict[str, Any]) -> None:
    pass


def _is_app(name: str) -> bool:
    return name == "app" or name.startswith("app.")


def _lint(*contracts: str) -> int:
    return lint_imports(config_filename=str(CONFIG), limit_to_contracts=contracts, no_cache=True, no_logo=True)


def test_every_contract_has_a_case() -> None:
    assert set(_contracts()) == set(CASES)


def test_unchanged_tree_keeps_every_contract(tree: Path) -> None:
    assert tree.is_dir()
    assert _lint() == 0


@pytest.mark.parametrize("contract", sorted(CASES))
def test_contract_fails_on_its_violation(tree: Path, contract: str) -> None:
    target, line = CASES[contract]
    path = tree / target
    path.write_text(path.read_text() + f"\n{line}\n")

    assert _lint(contract) != 0
