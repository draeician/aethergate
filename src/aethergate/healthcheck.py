"""Container healthcheck: exit 0 when the liveness endpoint responds.

Used by the Compose healthcheck so it does not depend on curl or shell quoting.
"""

from __future__ import annotations

import sys
from urllib.request import urlopen


def main() -> int:
    try:
        with urlopen("http://127.0.0.1:8000/health/live", timeout=3) as response:
            return 0 if response.status == 200 else 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
