"""Beta 1 release-script safety: failure-path teardown and collision guards.

The local acceptance scripts must never adopt or tear down an unrelated compose
project. These tests prove, with a deterministic fake ``docker`` on ``PATH``,
that each script fails closed (non-zero exit, no build/up) when its fixed compose
project name already owns resources, and that each script declares an
EXIT/INT/TERM teardown trap plus a ``STARTED`` ownership flag so a ``down -v``
runs only for the project the script itself created.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_GATE = REPO_ROOT / "scripts" / "ci" / "beta_release_gate.sh"
NOMNOM = REPO_ROOT / "scripts" / "ci" / "beta_nomnom_acceptance.sh"


@pytest.fixture
def fake_docker(tmp_path):
    """A docker shim that reports a leftover project container.

    ``docker ps -aq ...`` returns a fake container ID (so ``project_exists`` is
    true); network/volume queries return empty; any other invocation fails loudly,
    so the test proves the script dies at the guard before reaching ``build``.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "docker"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "ps" ]]; then echo fakeleftover123; exit 0; fi\n'
        'if [[ "$1" == "network" ]]; then exit 0; fi\n'
        'if [[ "$1" == "volume" ]]; then exit 0; fi\n'
        'echo "UNEXPECTED docker $*" >&2; exit 99\n'
    )
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def _run(script: Path, bin_dir: Path, tmp_path: Path, env_name: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    env[env_name] = str(tmp_path / "work")
    return subprocess.run(
        ["bash", str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_release_gate_fails_closed_on_project_collision(tmp_path, fake_docker):
    result = _run(RELEASE_GATE, fake_docker, tmp_path, "BETA_SMOKE_WORKDIR")
    assert result.returncode != 0
    assert "already exists" in result.stderr
    assert "UNEXPECTED docker" not in result.stderr


def test_nomnom_acceptance_fails_closed_on_project_collision(tmp_path, fake_docker):
    result = _run(NOMNOM, fake_docker, tmp_path, "BETA_NOMNOM_WORKDIR")
    assert result.returncode != 0
    assert "already exists" in result.stderr
    assert "UNEXPECTED docker" not in result.stderr


@pytest.mark.parametrize("script", [RELEASE_GATE, NOMNOM])
def test_scripts_declare_teardown_trap_and_ownership_flag(script):
    text = script.read_text()
    assert "STARTED=0" in text
    assert "trap cleanup EXIT" in text
    assert "trap 'cleanup; exit 130' INT" in text
    assert "trap 'cleanup; exit 143' TERM" in text
    # Teardown is gated on ownership and only runs `down -v` on its own project.
    assert 'if [[ "$STARTED" == "1" ]]' in text
    assert "compose down -v" in text
