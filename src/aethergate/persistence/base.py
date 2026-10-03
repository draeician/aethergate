"""Declarative base for v2 persistence models."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all v2 ORM models."""
