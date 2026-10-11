"""Internal secret-resolution interface.

The database may store secret-reference *metadata* (a ``SecretRef``), but never
the material itself. Resolvers return material only inside the trusted runtime,
and the value never appears in DTOs, logs, ``repr`` output, migrations, or test
snapshots.

``EnvSecretResolver`` is a development/test convenience and is explicitly not the
final enterprise secret backend (Vault/KMS/envelope encryption remain future work).
"""

from __future__ import annotations

import os
from typing import Protocol

from aethergate.domain.entities import SecretRef
from aethergate.errors import SecretResolutionError


class SecretResolver(Protocol):
    """Resolve a secret reference to its material inside the trusted runtime."""

    def resolve(self, ref: SecretRef) -> str:
        """Return the secret material for ``ref``.

        Raises:
            SecretResolutionError: if the reference cannot be resolved.
        """
        ...


class EnvSecretResolver:
    """Resolve secrets from ``AETHERGATE_SECRET_*`` environment variables.

    Development/test only. Never use this as the production secret backend.
    """

    def __init__(self, prefix: str = "AETHERGATE_SECRET_") -> None:
        self._prefix = prefix

    def _variable_name(self, ref: SecretRef) -> str:
        normalized = ref.name.replace("-", "_").replace(" ", "_").upper()
        return f"{self._prefix}{normalized}"

    def resolve(self, ref: SecretRef) -> str:
        variable = self._variable_name(ref)
        value = os.environ.get(variable)
        if value is None:
            raise SecretResolutionError(
                f"secret {ref.name!r} not resolvable from environment"
            )
        return value
