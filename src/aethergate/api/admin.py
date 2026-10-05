"""Protected admin HTTP surface (identity/RBAC/bootstrap + CRUD).

The control plane exposes:

- ``POST /admin/v1/bootstrap`` — one-use bootstrap (establishes ``system_admin``).
- ``GET  /admin/v1/whoami`` — safe admin identity metadata.
- project CRUD (list/create/read/patch) with non-enumerating scope resolution.
- principal CRUD (list/create/read/patch) with non-enumerating opaque IDs.
- role-assignment create/list/read/revoke with centralized escalation defense.
- credential create/list/read/rotate/revoke — generic (either audience), with
  non-enumerating opaque IDs.

Routers are thin: authorization lives in
:mod:`aethergate.identity.authorization` and the mutation services in
:mod:`aethergate.identity.admin`, which accept a typed
``AdminRequestContext`` and authorize internally. Routers never compare opaque
resource IDs to project IDs directly.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.contracts.admin_v1 import (
    ApiCredentialCreate,
    ApiCredentialCreateResult,
    ApiCredentialRead,
    ApiCredentialRevokeResult,
    BootstrapResult,
    ExternalIdentityCreate,
    ExternalIdentityRead,
    PrincipalCreate,
    PrincipalRead,
    PrincipalUpdate,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
    RoleAssignmentCreate,
    RoleAssignmentRead,
    WhoamiRead,
)
from aethergate.contracts.common import Page
from aethergate.domain import entities as domain
from aethergate.domain.enums import AdminAuthenticationKind, CredentialScope
from aethergate.domain.ids import ApiCredentialId, PrincipalId, ProjectId, RoleAssignmentId
from aethergate.errors import (
    AdminAuthenticationRequired,
    AdminAuthorizationError,
    AuthenticationRequired,
    CredentialLifecycleError,
)
from aethergate.identity import admin as admin_service
from aethergate.identity import service as identity_service
from aethergate.identity import session as session_service
from aethergate.identity.authorization import (
    RESOURCE_CREDENTIAL,
    RESOURCE_PRINCIPAL,
    RESOURCE_PROJECT,
    RESOURCE_ROLE_ASSIGNMENT,
    authorized_project_ids,
    resolve_admin_resource,
)
from aethergate.persistence import repository
from aethergate.persistence.db import get_session, get_session_factory

router = APIRouter(prefix="/admin/v1", tags=["admin"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

LimitQuery = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
OffsetQuery = Annotated[int, Query(ge=0)]


async def _admin_context(request: Request) -> domain.AdminRequestContext:
    auth_values = request.headers.getlist("authorization")
    if auth_values:
        # A supplied Authorization header is authoritative: parse and authenticate
        # it strictly; an invalid header never falls through to a cookie.
        try:
            token = identity_service.parse_bearer_token(auth_values)
        except AuthenticationRequired:
            raise AdminAuthenticationRequired() from None
        async with get_session_factory()() as session:
            async with session.begin():
                return await admin_service.authenticate_admin(session, token)

    raw_cookie = request.cookies.get(session_service.SESSION_COOKIE_NAME)
    if not raw_cookie:
        raise AdminAuthenticationRequired()
    async with get_session_factory()() as session:
        async with session.begin():
            context, session_entity = await admin_service.authenticate_browser_session(
                session, raw_cookie
            )
            if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                session_service.validate_csrf(
                    session_entity, request.headers.get(session_service.CSRF_HEADER_NAME)
                )
    return context


AdminContextDep = Annotated[domain.AdminRequestContext, Depends(_admin_context)]


# --- converters ----------------------------------------------------------------


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


def _to_project(project: domain.Project) -> ProjectRead:
    return ProjectRead(id=project.id, name=project.name, is_active=project.is_active)


def _to_principal(principal: domain.Principal) -> PrincipalRead:
    return PrincipalRead(
        id=principal.id,
        project_id=principal.project_id,
        kind=principal.kind,
        name=principal.name,
        is_active=principal.is_active,
    )


def _to_assignment(assignment: domain.RoleAssignment) -> RoleAssignmentRead:
    return RoleAssignmentRead(
        id=assignment.id,
        principal_id=assignment.principal_id,
        role=assignment.role,
        resource_scope_type=assignment.resource_scope_type,
        resource_id=assignment.resource_id,
        created_at=assignment.created_at,
        created_by=assignment.created_by,
        revoked_at=assignment.revoked_at,
        is_active=assignment.is_active,
    )


def _require_scope(context: domain.AdminRequestContext, permission: CredentialScope) -> None:
    """Enforce that a service credential carries ``permission`` (list endpoints).

    A human browser session has no credential scopes and is authorized by role
    assignment alone, so this check is skipped for ``BROWSER_SESSION``.
    """
    if (
        context.authentication_kind is AdminAuthenticationKind.SERVICE_CREDENTIAL
        and permission not in context.scopes
    ):
        raise AdminAuthorizationError()


def _bootstrap_token(request: Request) -> str | None:
    try:
        return identity_service.parse_bearer_token(
            request.headers.getlist("authorization")
        )
    except AuthenticationRequired:
        return None


# --- bootstrap / whoami --------------------------------------------------------


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
        authentication_kind=context.authentication_kind,
        api_credential_id=context.api_credential_id,
        browser_session_id=context.browser_session_id,
        audience=context.audience,
        scopes=context.scopes,
        roles=context.roles,
    )


# --- projects -------------------------------------------------------------------


@router.post("/projects", response_model=ProjectRead, status_code=201)
async def create_project(
    body: ProjectCreate, context: AdminContextDep, session: SessionDep
) -> ProjectRead:
    async with session.begin():
        project = await admin_service.create_project(session, context=context, name=body.name)
    return _to_project(project)


@router.get("/projects", response_model=Page[ProjectRead])
async def list_projects(
    context: AdminContextDep,
    session: SessionDep,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[ProjectRead]:
    _require_scope(context, CredentialScope.ADMIN_PROJECTS_READ)
    project_ids = authorized_project_ids(context)
    items = await repository.list_projects(
        session, project_ids=project_ids, limit=limit, offset=offset
    )
    total = await repository.count_projects(session, project_ids=project_ids)
    return Page(items=[_to_project(p) for p in items], limit=limit, offset=offset, total=total)


@router.get("/projects/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: ProjectId, context: AdminContextDep, session: SessionDep
) -> ProjectRead:
    project = await resolve_admin_resource(
        session, context, CredentialScope.ADMIN_PROJECTS_READ, RESOURCE_PROJECT, str(project_id)
    )
    assert isinstance(project, domain.Project)
    return _to_project(project)


@router.patch("/projects/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: ProjectId,
    body: ProjectUpdate,
    context: AdminContextDep,
    session: SessionDep,
) -> ProjectRead:
    async with session.begin():
        project = await admin_service.update_project(
            session,
            context=context,
            project_id=project_id,
            name=body.name,
            is_active=body.is_active,
        )
    return _to_project(project)


# --- principals -----------------------------------------------------------------


@router.post("/projects/{project_id}/principals", response_model=PrincipalRead, status_code=201)
async def create_principal(
    project_id: ProjectId,
    body: PrincipalCreate,
    context: AdminContextDep,
    session: SessionDep,
) -> PrincipalRead:
    async with session.begin():
        principal = await admin_service.create_principal(
            session,
            context=context,
            project_id=project_id,
            kind=body.kind,
            name=body.name,
        )
    return _to_principal(principal)


@router.get("/projects/{project_id}/principals", response_model=Page[PrincipalRead])
async def list_principals(
    project_id: ProjectId,
    context: AdminContextDep,
    session: SessionDep,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[PrincipalRead]:
    await resolve_admin_resource(
        session, context, CredentialScope.ADMIN_PRINCIPALS_READ, RESOURCE_PROJECT, str(project_id)
    )
    items = await repository.list_principals(session, project_id, limit=limit, offset=offset)
    total = await repository.count_principals(session, project_id)
    return Page(items=[_to_principal(p) for p in items], limit=limit, offset=offset, total=total)


@router.get("/principals/{principal_id}", response_model=PrincipalRead)
async def get_principal(
    principal_id: PrincipalId, context: AdminContextDep, session: SessionDep
) -> PrincipalRead:
    principal = await resolve_admin_resource(
        session,
        context,
        CredentialScope.ADMIN_PRINCIPALS_READ,
        RESOURCE_PRINCIPAL,
        str(principal_id),
    )
    assert isinstance(principal, domain.Principal)
    return _to_principal(principal)


@router.patch("/principals/{principal_id}", response_model=PrincipalRead)
async def update_principal(
    principal_id: PrincipalId,
    body: PrincipalUpdate,
    context: AdminContextDep,
    session: SessionDep,
) -> PrincipalRead:
    async with session.begin():
        principal = await admin_service.update_principal(
            session,
            context=context,
            principal_id=principal_id,
            name=body.name,
            is_active=body.is_active,
        )
    return _to_principal(principal)


# --- role assignments ----------------------------------------------------------


@router.post("/role-assignments", response_model=RoleAssignmentRead, status_code=201)
async def create_role_assignment(
    body: RoleAssignmentCreate, context: AdminContextDep, session: SessionDep
) -> RoleAssignmentRead:
    async with session.begin():
        assignment = await admin_service.create_role_assignment(
            session,
            context=context,
            principal_id=body.principal_id,
            role=body.role,
            resource_scope_type=body.resource_scope_type,
            resource_id=body.resource_id,
        )
    return _to_assignment(assignment)


@router.get("/role-assignments", response_model=Page[RoleAssignmentRead])
async def list_role_assignments(
    context: AdminContextDep,
    session: SessionDep,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[RoleAssignmentRead]:
    _require_scope(context, CredentialScope.ADMIN_PRINCIPALS_READ)
    project_ids = authorized_project_ids(context)
    items = await repository.list_role_assignments(
        session, project_ids=project_ids, limit=limit, offset=offset
    )
    total = await repository.count_role_assignments(session, project_ids=project_ids)
    return Page(
        items=[_to_assignment(a) for a in items], limit=limit, offset=offset, total=total
    )


@router.get("/role-assignments/{assignment_id}", response_model=RoleAssignmentRead)
async def get_role_assignment(
    assignment_id: RoleAssignmentId, context: AdminContextDep, session: SessionDep
) -> RoleAssignmentRead:
    assignment = await resolve_admin_resource(
        session,
        context,
        CredentialScope.ADMIN_PRINCIPALS_READ,
        RESOURCE_ROLE_ASSIGNMENT,
        str(assignment_id),
    )
    assert isinstance(assignment, domain.RoleAssignment)
    return _to_assignment(assignment)


@router.post("/role-assignments/{assignment_id}/revoke", response_model=RoleAssignmentRead)
async def revoke_role_assignment(
    assignment_id: RoleAssignmentId, context: AdminContextDep, session: SessionDep
) -> RoleAssignmentRead:
    async with session.begin():
        assignment = await admin_service.revoke_role_assignment(
            session, context=context, assignment_id=assignment_id
        )
    return _to_assignment(assignment)


# --- credentials ----------------------------------------------------------------


@router.post("/credentials", response_model=ApiCredentialCreateResult, status_code=201)
async def create_credential(
    body: ApiCredentialCreate, context: AdminContextDep, session: SessionDep
) -> ApiCredentialCreateResult:
    if body.principal_id is None:
        raise CredentialLifecycleError("principal_id is required to create a credential")
    async with session.begin():
        credential, raw_key = await admin_service.create_credential(
            session,
            context=context,
            project_id=body.project_id,
            principal_id=body.principal_id,
            name=body.name,
            audience=body.audience,
            scopes=body.scopes,
            expires_at=body.expires_at,
        )
    return ApiCredentialCreateResult(credential=_to_read(credential), raw_key=raw_key)


@router.get("/projects/{project_id}/credentials", response_model=Page[ApiCredentialRead])
async def list_credentials(
    project_id: ProjectId,
    context: AdminContextDep,
    session: SessionDep,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[ApiCredentialRead]:
    await resolve_admin_resource(
        session, context, CredentialScope.ADMIN_CREDENTIALS_READ, RESOURCE_PROJECT, str(project_id)
    )
    items = await repository.list_api_credentials_paged(
        session, project_id, limit=limit, offset=offset
    )
    total = await repository.count_api_credentials(session, project_id)
    return Page(items=[_to_read(c) for c in items], limit=limit, offset=offset, total=total)


@router.get("/credentials/{credential_id}", response_model=ApiCredentialRead)
async def get_credential(
    credential_id: ApiCredentialId, context: AdminContextDep, session: SessionDep
) -> ApiCredentialRead:
    credential = await resolve_admin_resource(
        session,
        context,
        CredentialScope.ADMIN_CREDENTIALS_READ,
        RESOURCE_CREDENTIAL,
        str(credential_id),
    )
    assert isinstance(credential, domain.ApiCredential)
    return _to_read(credential)


@router.post("/credentials/{credential_id}/rotate", response_model=ApiCredentialCreateResult)
async def rotate_credential(
    credential_id: ApiCredentialId, context: AdminContextDep, session: SessionDep
) -> ApiCredentialCreateResult:
    async with session.begin():
        credential, raw_key = await admin_service.rotate_credential(
            session, context=context, credential_id=credential_id
        )
    return ApiCredentialCreateResult(credential=_to_read(credential), raw_key=raw_key)


@router.post("/credentials/{credential_id}/revoke", response_model=ApiCredentialRevokeResult)
async def revoke_credential(
    credential_id: ApiCredentialId, context: AdminContextDep, session: SessionDep
) -> ApiCredentialRevokeResult:
    async with session.begin():
        credential = await admin_service.revoke_credential(
            session, context=context, credential_id=credential_id
        )
    return ApiCredentialRevokeResult(credential=_to_read(credential))


# --- OIDC external identity linking --------------------------------------------


def _to_external_identity(identity: domain.ExternalIdentity) -> ExternalIdentityRead:
    return ExternalIdentityRead(
        id=identity.id,
        principal_id=identity.principal_id,
        issuer=identity.issuer,
        subject=identity.subject,
        email=identity.email,
        display_name=identity.display_name,
        created_at=identity.created_at,
        last_login_at=identity.last_login_at,
        is_active=identity.is_active,
    )


@router.post("/oidc/identities", response_model=ExternalIdentityRead, status_code=201)
async def link_oidc_identity(
    body: ExternalIdentityCreate, context: AdminContextDep, session: SessionDep
) -> ExternalIdentityRead:
    async with session.begin():
        identity = await admin_service.link_external_identity(
            session,
            context=context,
            principal_id=body.principal_id,
            issuer=body.issuer,
            subject=body.subject,
        )
    return _to_external_identity(identity)


__all__ = ["router"]
