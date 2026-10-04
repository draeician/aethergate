"""Development-only stable request identity for the temporary auth bypass.

While the explicit development inference auth bypass from AGV2-004 exists, the
scheduler still needs stable attribution (project/principal/credential) rather
than random per-request values. This module provides an idempotent seeded
"dev" identity and a resolver that is only ever consulted when the bypass is
enabled. Production mode fails closed in the config layer and never reaches this
code path.

This is development/bootstrap tooling, not the final OIDC/auth system.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.domain import entities as domain
from aethergate.domain.enums import PrincipalKind
from aethergate.domain.ids import (
    ApiCredentialId,
    PrincipalId,
    ProjectId,
    SecretRefId,
)
from aethergate.persistence import repository

DEV_PROJECT_NAME = "dev"
DEV_PRINCIPAL_NAME = "dev"
DEV_CREDENTIAL_NAME = "dev"
DEV_SECRET_REF_NAME = "dev-credential"


def _new_id() -> str:
    return uuid.uuid4().hex


async def ensure_dev_identity(
    session: AsyncSession,
) -> tuple[ProjectId, PrincipalId, ApiCredentialId]:
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
        secret_ref = domain.SecretRef(
            id=SecretRefId(_new_id()),
            name=DEV_SECRET_REF_NAME,
            created_at=datetime.now(UTC),
        )
        # The dev credential references a placeholder secret ref; its material is
        # never resolved in the bypass path.
        created_ref = await repository.create_secret_ref(session, secret_ref)
        credential = await repository.create_api_credential(
            session,
            domain.ApiCredential(
                id=ApiCredentialId(_new_id()),
                project_id=project.id,
                principal_id=principal.id,
                name=DEV_CREDENTIAL_NAME,
                secret_ref_id=created_ref.id,
            ),
        )

    return project.id, principal.id, credential.id
