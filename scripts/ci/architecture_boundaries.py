#!/usr/bin/env python3
"""Executable architecture-boundary check for AetherGate v2.

Enforces the dependency-direction rules asserted in
``docs/development/agent-workstreams.md`` and ``project_spec.md`` without any
third-party dependencies (stdlib ``ast`` only), so it is safe to run in CI before
``pip install``.

Invariants (each maps to a settled rule):

1. **Bottom does not reach up.** ``aethergate.persistence`` (the SQLAlchemy model
   and repository layer) must never import a higher service/interface layer
   (``api``, ``cli``, ``scheduler``, ``accounting``, ``catalog``, ``identity``,
   ``observability``, ``inference``, ``adapters``) nor the ``main`` entrypoint.
   The persistence layer is the data authority; it may depend only on config,
   domain, errors, and itself.

2. **CLI is a thin HTTP client.** ``aethergate.cli`` must never import
   ``aethergate.persistence``; every product command talks to ``/admin/v1``.

3. **Routers do not touch raw ORM models.** ``aethergate.api`` must never import
   ``aethergate.persistence.models`` directly (routers are thin and go through
   services/repositories, never raw SQLAlchemy tables).

This is a *ratcheted* check: it validates the current tree and will fail the
build on any new violation. Known acceptable patterns (read-only repository
access from routers, ``api.health`` importing ``persistence.db`` for the
readiness probe) remain permitted because they do not place database *policy*
in a router.

Exit code 0 means all invariants hold; nonzero prints each offending import.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "aethergate"

# Higher layers that the persistence (bottom) layer must never reach up into.
HIGHER_PACKAGES = {
    "aethergate.api",
    "aethergate.cli",
    "aethergate.scheduler",
    "aethergate.accounting",
    "aethergate.catalog",
    "aethergate.identity",
    "aethergate.observability",
    "aethergate.inference",
    "aethergate.adapters",
}


def _module_of(path: Path) -> str:
    relative = path.relative_to(SRC_ROOT)
    parts = list(relative.with_suffix("").parts)
    return ".".join(["aethergate", *parts])


def _top_package(module: str) -> str:
    parts = module.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else module


def _imported_names(tree: ast.Module) -> list[str]:
    """Fully-qualified names for every import (``import a.b`` / ``from a.b import c``)."""
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                names.append(f"{node.module}.{alias.name}")
    return names


def _violations() -> list[str]:
    problems: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        module = _module_of(path)
        package = _top_package(module)
        tree = ast.parse(path.read_text(), filename=str(path))
        for imported in _imported_names(tree):
            if package == "aethergate.cli" and _top_package(imported) == "aethergate.persistence":
                problems.append(f"{module}: CLI imports persistence ({imported})")
            if package == "aethergate.api" and (
                imported == "aethergate.persistence.models"
                or imported.startswith("aethergate.persistence.models.")
            ):
                problems.append(f"{module}: router imports raw ORM models ({imported})")
            if package == "aethergate.persistence" and _top_package(imported) in HIGHER_PACKAGES:
                problems.append(f"{module}: persistence imports higher layer ({imported})")
            if package == "aethergate.persistence" and imported == "aethergate.main":
                problems.append(f"{module}: persistence imports entrypoint ({imported})")
    return problems


def main() -> int:
    problems = _violations()
    if problems:
        print("architecture boundary violations:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("architecture boundaries: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
