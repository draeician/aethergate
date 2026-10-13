"""Architecture-boundary check: relative-import resolution regression tests.

The executable check ``scripts/ci/architecture_boundaries.py`` must not be
defeated by relative imports. These tests load it as a module and prove that:

- ``from ..persistence import models`` inside an API module resolves to the
  absolute ``aethergate.persistence.models`` and is flagged (the pre-fix code
  reported only the bare ``persistence.models`` tail and missed it);
- ``from . import ...`` resolves against the containing package;
- a relative import from persistence to a higher layer is flagged;
- permitted imports (repositories, absolute imports) are not false-flagged;
- the current tree still passes (ratchet).
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "ci"
    / "architecture_boundaries.py"
)
_spec = importlib.util.spec_from_file_location("architecture_boundaries", SCRIPT)
assert _spec is not None and _spec.loader is not None
boundaries = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(boundaries)


def _imports(module: str, source: str) -> list[str]:
    return boundaries._imported_names(module, ast.parse(source))


def _violations(module: str, source: str) -> list[str]:
    return boundaries._module_violations(module, ast.parse(source))


def test_relative_import_resolves_against_containing_package():
    # `from ..persistence import models` in an api module -> aethergate.persistence.models
    assert _imports("aethergate.api.health", "from ..persistence import models") == [
        "aethergate.persistence.models"
    ]
    assert _imports("aethergate.api.health", "from .. import persistence") == [
        "aethergate.persistence"
    ]
    # `from . import x` and `from .pkg import x` resolve against the package itself.
    assert _imports("aethergate.api.routes.foo", "from . import db") == [
        "aethergate.api.routes.db"
    ]
    assert _imports("aethergate.api.routes.foo", "from .persistence import models") == [
        "aethergate.api.routes.persistence.models"
    ]
    # A three-level relative import drops two package components.
    assert _imports("aethergate.api.routes.foo", "from ...errors import DomainError") == [
        "aethergate.errors.DomainError"
    ]


def test_absolute_import_resolution_is_unchanged():
    assert _imports("aethergate.api.health", "import aethergate.persistence.db") == [
        "aethergate.persistence.db"
    ]
    assert _imports(
        "aethergate.api.health", "from aethergate.persistence.db import get_engine"
    ) == ["aethergate.persistence.db.get_engine"]


def test_relative_import_of_raw_orm_models_is_flagged():
    problems = _violations("aethergate.api.health", "from ..persistence import models")
    assert any("router imports raw ORM models" in p for p in problems)


def test_relative_import_of_repository_is_not_false_flagged():
    # Routers may reach persistence through repositories, not raw ORM models.
    problems = _violations(
        "aethergate.api.health", "from ..persistence import repository"
    )
    assert problems == []


def test_relative_import_up_from_persistence_is_flagged():
    problems = _violations("aethergate.persistence.models", "from ..api import service")
    assert any("persistence imports higher layer" in p for p in problems)


def test_relative_import_of_entrypoint_from_persistence_is_flagged():
    problems = _violations("aethergate.persistence.models", "from .. import main")
    assert any("persistence imports entrypoint" in p for p in problems)


def test_relative_import_of_persistence_from_cli_is_flagged():
    problems = _violations("aethergate.cli.main", "from ..persistence import db")
    assert any("CLI imports persistence" in p for p in problems)


def test_current_tree_has_no_boundary_violations():
    assert boundaries._violations() == []
