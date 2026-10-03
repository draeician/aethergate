"""Persistence layer: SQLAlchemy models, session, and repositories.

Models here are separate from the domain contracts in ``aethergate.domain``.
Repositories map between ORM rows and domain entities and never let ORM objects
escape upward.
"""

from __future__ import annotations

from aethergate.persistence import base, db, models, repository

__all__ = ["base", "db", "models", "repository"]
