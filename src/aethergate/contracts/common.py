"""Shared contract primitives (not resource-specific)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Base for public contract DTOs."""

    model_config = ConfigDict(extra="forbid")


class ApiErrorBody(ContractModel):
    """Structured error envelope returned by the gateway."""

    type: str
    message: str
    code: str | None = None
    request_id: str | None = None


class Page[T](ContractModel):
    items: list[T]
    limit: int
    offset: int
    total: int


__all__ = ["ContractModel", "ApiErrorBody", "Page"]
