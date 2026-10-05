"""Minimal protected admin HTTP surface (identity/RBAC/bootstrap).

Not full admin CRUD — just enough to prove the admin identity model:
``POST /admin/v1/bootstrap`` (one-use), ``GET /admin/v1/whoami``, and a real
credential list/create/rotate/revoke action. All future admin CRUD must reuse
these dependencies and the centralized authorization service.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.api.deps import get_gateway_request_id
from aethergate.contracts.admin_v1 import (
    ApiCredentialCreate,
    ApiCredentialCreateResult,
    ApiCredentialRead,
    ApiCredentialRevokeResult,
    BootstrapResult,
    WhoamiRead,
)
from aethergate.domain import entities as domain
from aethergate.domain.enums import CredentialScope
from aethergate.domain.ids import ApiCredentialId, ProjectId
from aethergate.errors import (
    AdminAuthenticationRequired,
    AuthenticationRequired,
    CredentialLifecycleError,
)
from aethergate.identity import admin as admin_service
from aethergate.identity import service as identity_service
from aethergate.persistence import repository
from aethergate.persistence.db import get_session, get_session_factory

router = APIRouter(prefix="/admin/v1", tags=["admin"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _admin_context(request: Request) -> domain.AdminRequestContext:
    auth_values = request.headers.getlist("authorization")
    try:
        token = identity_service.parse_bearer_token(auth_values)
    except AuthenticationRequired:
        raise AdminAuthenticationRequired() from None
    async with get_session_factory()() as session:
        async with session.begin():
            return await admin_service.authenticate_admin(session, token)


AdminContextDep = Annotated[domain.AdminRequestContext, Depends(_admin_context)]


def _to_read(credential: domain.ApiCredential) -> ApiCredentialRead:
    return ApiCredentialRead(
        id=credential.id,
        project_id=credential.project_id,
        principal_id=credential.principal_id,
        name=credential.name,
        key_prefix=credential.key_prefix,
        audience=credential.audience,
        scopes=credential.scopes,
        created_at=credential.created_at,
        expires_at=credential.expires_at,
        revoked_at=credential.revoked_at,
        is_active=credential.is_active,
    )


def _not_found(request_id: str | None) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": {"code": "not_found", "message": "Resource not found.",
                           "request_id": request_id}},
    )


def _bootstrap_token(request: Request) -> str | None:
    try:
        return identity_service.parse_bearer_token(
            request.headers.getlist("authorization")
        )
    except AuthenticationRequired:
        return None


@router.post("/bootstrap", response_model=BootstrapResult, status_code=201)
async def bootstrap(request: Request, session: SessionDep) -> BootstrapResult:
    credential, raw_key = await admin_service.bootstrap(
        session, _bootstrap_token(request)
    )
    return BootstrapResult(credential=_to_read(credential), raw_key=raw_key)


@router.get("/whoami", response_model=WhoamiRead)
async def whoami(context: AdminContextDep) -> WhoamiRead:
    return WhoamiRead(
        principal_id=context.principal_id,
        project_id=context.project_id,
        api_credential_id=context.api_credential_id,
        audience=context.audience,
        scopes=context.scopes,
        roles=context.roles,
    )


@router.get("/projects/{project_id}/credentials", response_model=list[ApiCredentialRead])
async def list_credentials(
    project_id: ProjectId, context: AdminContextDep, session: SessionDep
) -> list[ApiCredentialRead]:
    admin_service.authorize_admin(
        context, CredentialScope.ADMIN_CREDENTIALS_READ, "project", project_id
    )
    credentials = await identity_service.list_credentials(session, project_id)
    return [_to_read(c) for c in credentials]


@router.post("/credentials", response_model=ApiCredentialCreateResult, status_code=201)
async def create_credential(
    body: ApiCredentialCreate, context: AdminContextDep, session: SessionDep
) -> ApiCredentialCreateResult:
    if body.principal_id is None:
        raise CredentialLifecycleError("principal_id is required to create a credential")
    admin_service.authorize_admin(
        context, CredentialScope.ADMIN_CREDENTIALS_WRITE, "project", body.project_id
    )
    async with session.begin():
        credential, raw_key = await admin_service.create_admin_credential(
            session,
            actor_principal_id=context.principal_id,
            project_id=body.project_id,
            principal_id=body.principal_id,
            name=body.name,
            audience=body.audience,
            scopes=body.scopes,
            expires_at=body.expires_at,
        )
    return ApiCredentialCreateResult(credential=_to_read(credential), raw_key=raw_key)


@router.post("/credentials/{credential_id}/rotate", response_model=ApiCredentialCreateResult)
async def rotate_credential(
    credential_id: ApiCredentialId, request: Request, context: AdminContextDep,
    session: SessionDep,
) -> ApiCredentialCreateResult:
    async with session.begin():
        existing = await repository.get_api_credential(session, credential_id)
        if existing is None:
            return _not_found(get_gateway_request_id(request))
        admin_service.authorize_admin(
            context, CredentialScope.ADMIN_CREDENTIALS_WRITE, "project", existing.project_id
        )
        credential, raw_key = await admin_service.rotate_admin_credential(
            session,
            actor_principal_id=context.principal_id,
            credential_id=credential_id,
        )
    return ApiCredentialCreateResult(credential=_to_read(credential), raw_key=raw_key)


@router.post("/credentials/{credential_id}/revoke", response_model=ApiCredentialRevokeResult)
async def revoke_credential(
    credential_id: ApiCredentialId, request: Request, context: AdminContextDep,
    session: SessionDep,
) -> ApiCredentialRevokeResult:
    async with session.begin():
        existing = await repository.get_api_credential(session, credential_id)
        if existing is None:
            return _not_found(get_gateway_request_id(request))
        admin_service.authorize_admin(
            context, CredentialScope.ADMIN_CREDENTIALS_WRITE, "project", existing.project_id
        )
        credential = await admin_service.revoke_admin_credential(
            session,
            actor_principal_id=context.principal_id,
            credential_id=credential_id,
        )
    assert credential is not None
    return ApiCredentialRevokeResult(credential=_to_read(credential))


__all__ = ["router"]
