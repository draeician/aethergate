"""Package-level sanity checks."""

from __future__ import annotations

import aethergate


def test_package_imports_from_clean_process():
    from aethergate import contracts, domain  # noqa: F401

    assert aethergate.__version__ == "0.1.0"


def test_version_matches_packaging_metadata():
    assert isinstance(aethergate.__version__, str)
    assert aethergate.__version__ != ""
