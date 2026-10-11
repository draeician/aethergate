"""Export the FastAPI OpenAPI schema deterministically for client generation.

Writes the v2 admin+inference OpenAPI document to ``frontend/src/generated/
openapi.json`` so the TypeScript client/types can be generated without a running
server. The output is sorted and stable, enabling drift detection in CI/dev.

Usage:
    python scripts/gen_openapi.py [output-path]

The application import is side-effect-free with respect to the database: routes
are registered at import time and ``app.openapi()`` only introspects them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from aethergate.main import app

DEFAULT_OUTPUT = Path("frontend/src/generated/openapi.json")


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    schema = app.openapi()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(schema, handle, sort_keys=True, indent=2)
        handle.write("\n")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
