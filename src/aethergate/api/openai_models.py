"""OpenAI-compatible model listing and retrieval."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.api.deps import require_inference_access
from aethergate.contracts.openai import ModelList, ModelObject
from aethergate.errors import ModelAliasNotFound
from aethergate.persistence import repository
from aethergate.persistence.db import get_session

router = APIRouter(tags=["models"])

AuthDep = Annotated[None, Depends(require_inference_access)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/v1/models", response_model=ModelList)
async def list_models(
    _: AuthDep,
    session: SessionDep,
) -> ModelList:
    aliases = await repository.list_active_model_aliases(session)
    return ModelList(data=[ModelObject(id=alias.name) for alias in aliases])


@router.get("/v1/models/{model}", response_model=ModelObject)
async def get_model(
    model: str,
    _: AuthDep,
    session: SessionDep,
) -> ModelObject:
    alias = await repository.get_model_alias_by_name(session, model)
    if alias is None or not alias.is_active:
        raise ModelAliasNotFound(model)
    return ModelObject(id=alias.name)
