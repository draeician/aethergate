"""Development-only stable request identity for the temporary auth bypass.

While the explicit development inference auth bypass from AGV2-004 exists, the
scheduler still needs stable attribution (project/principal/credential) rather
than random per-request values. This module provides an idempotent seeded
"dev" identity that is only ever consulted when the bypass is enabled AND no
Authorization header is supplied. Production mode fails closed in the config
layer and never reaches this code path.

The dev credential is a synthetic identity with no verifiable key (``key_hash``
is NULL), so it can never be presented as a real Bearer token. It carries the
inference audience and ``inference:invoke`` scope so pre-dispatch authorization
revalidation treats it consistently with real credentials.

This is development/bootstrap tooling, not the final OIDC/auth system.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.domain import entities as domain
from aethergate.domain.entities import RequestContext
from aethergate.domain.enums import CredentialAudience, CredentialScope, PrincipalKind
from aethergate.domain.ids import ApiCredentialId, PrincipalId, ProjectId
from aethergate.persistence import repository

DEV_PROJECT_NAME = "dev"
DEV_PRINCIPAL_NAME = "dev"
DEV_CREDENTIAL_NAME = "dev"


def _new_id() -> str:
    return uuid.uuid4().hex


async def ensure_dev_identity(session: AsyncSession) -> RequestContext:
    """Idempotently seed and return the stable development identity context."""
    project = await repository.get_project_by_name(session, DEV_PROJECT_NAME)
    if project is None:
        project = await repository.create_project(
            session,
            domain.Project(id=ProjectId(_new_id()), name=DEV_PROJECT_NAME),
        )

    principal = await repository.get_principal_by_name(
        session, project.id, DEV_PRINCIPAL_NAME
    )
    if principal is None:
        principal = await repository.create_principal(
            session,
            domain.Principal(
                id=PrincipalId(_new_id()),
                project_id=project.id,
                kind=PrincipalKind.USER,
                name=DEV_PRINCIPAL_NAME,
            ),
        )

    credential = await repository.get_api_credential_by_name(
        session, project.id, DEV_CREDENTIAL_NAME
    )
    if credential is None:
        credential = await repository.create_api_credential(
            session,
            domain.ApiCredential(
                id=ApiCredentialId(_new_id()),
                project_id=project.id,
                principal_id=principal.id,
                name=DEV_CREDENTIAL_NAME,
                audience=CredentialAudience.INFERENCE,
                scopes=(CredentialScope.INFERENCE_INVOKE,),
            ),
        )

    return RequestContext(
        project_id=project.id,
        principal_id=principal.id,
        api_credential_id=credential.id,
        audience=credential.audience,
        scopes=credential.scopes,
    )
