"""FastAPI application entrypoint for AetherGate v2."""

from __future__ import annotations

from fastapi import FastAPI

from aethergate import __version__
from aethergate.api.health import router as health_router

app = FastAPI(title="AetherGate", version=__version__)
app.include_router(health_router)
