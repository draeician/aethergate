"""Worker liveness healthcheck (container health probe).

The worker has no HTTP surface, so its container healthcheck reads a heartbeat
file the worker refreshes on a fixed cadence from a dedicated event-loop task
(see ``AETHERGATE_WORKER_HEARTBEAT_FILE`` and ``worker.run``). The cadence is
independent of the claim/dispatch loop, so a long-running dispatch does not make
a healthy worker appear stale. Exit code 0 means the heartbeat is fresh; 1 means
the worker is stalled or the file is missing.

Usage (from the worker container):

    python -m aethergate.worker_healthcheck

Freshness threshold is ``AETHERGATE_WORKER_HEARTBEAT_TIMEOUT_SECONDS``
(default 90s, well above ``AETHERGATE_WORKER_HEARTBEAT_SECONDS``).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def main() -> int:
    path = os.environ.get("AETHERGATE_WORKER_HEARTBEAT_FILE")
    if not path:
        # No heartbeat file configured: nothing to check, treat as healthy so an
        # unconfigured container healthcheck does not flap.
        return 0
    threshold = float(
        os.environ.get("AETHERGATE_WORKER_HEARTBEAT_TIMEOUT_SECONDS", "90")
    )
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        return 1
    if time.time() - mtime > threshold:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
