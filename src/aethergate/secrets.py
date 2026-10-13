"""Internal secret-resolution interface.

The database may store secret-reference *metadata* (a ``SecretRef``), but never
the material itself. Resolvers return material only inside the trusted runtime,
and the value never appears in DTOs, logs, ``repr`` output, migrations, or test
snapshots.

Two resolvers are provided:

- :class:`EnvSecretResolver` — reads ``AETHERGATE_SECRET_*`` environment
  variables. This is a development/test convenience and is **not** a production
  backend; production rejects it (see :func:`build_secret_resolver`).
- :class:`FileSecretResolver` — the production-suitable, read-only file backend
  for externally projected secrets (Docker/Kubernetes projected secrets, systemd
  credentials, or an operator-managed read-only mount). It resolves a
  ``SecretRef`` by its stable opaque ``id`` so user-controlled display names can
  never become path-traversal primitives.

:func:`build_secret_resolver` is the single runtime factory used by both the API
and the worker so the same policy is constructed everywhere.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Protocol

from aethergate.domain.entities import SecretRef
from aethergate.errors import SecretResolutionError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from aethergate.config import Settings

# Conservative upper bound on projected secret material. Real provider keys are
# far smaller; the bound keeps a misconfigured/attacker-writable path from
# turning into an unbounded read.
MAX_SECRET_FILE_BYTES = 64 * 1024


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


class FileSecretResolver:
    """Resolve secrets from a read-only directory of per-reference files.

    Each ``SecretRef`` maps to ``<directory>/<ref.id>``. The configured directory
    must be an absolute path and is resolved once at construction. Every lookup
    re-validates that the resolved target stays inside that directory, so a
    symlink or crafted component cannot escape the root.

    File semantics:
    - regular files (or symlinks whose resolved target is a regular file inside
      the root) only;
    - bounded size (``MAX_SECRET_FILE_BYTES``);
    - UTF-8 text only, no NUL bytes, non-empty;
    - exactly one trailing ``LF`` or ``CRLF`` is removed (a common artifact of
      shell/systemd/Kubernetes projection); arbitrary whitespace is preserved.
    """

    def __init__(
        self,
        directory: str,
        *,
        max_bytes: int = MAX_SECRET_FILE_BYTES,
    ) -> None:
        if not directory or not os.path.isabs(directory):
            raise ValueError("secret directory must be an absolute path")
        root = os.path.realpath(directory)
        if not os.path.isdir(root):
            raise ValueError("secret directory does not exist or is not a directory")
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self._root = root
        self._max_bytes = max_bytes

    def _filename(self, ref: SecretRef) -> str:
        """Return a safe single-component filename for ``ref``.

        Uses the stable opaque id, never the human display name, and rejects any
        component that could traverse the filesystem.
        """
        name = str(ref.id)
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or "\x00" in name
        ):
            raise SecretResolutionError(
                f"secret reference {ref.id!s} has an unsafe file identity"
            )
        return name

    def resolve(self, ref: SecretRef) -> str:
        candidate = os.path.join(self._root, self._filename(ref))
        resolved = os.path.realpath(candidate)
        if os.path.commonpath([self._root, resolved]) != self._root:
            raise SecretResolutionError(
                f"secret {ref.id!s} resolves outside the configured directory"
            )
        if not os.path.isfile(resolved):
            raise SecretResolutionError(f"secret {ref.id!s} is not a regular file")
        try:
            size = os.path.getsize(resolved)
        except OSError:
            raise SecretResolutionError(
                f"secret {ref.id!s} could not be inspected"
            ) from None
        if size > self._max_bytes:
            raise SecretResolutionError(f"secret {ref.id!s} exceeds the size limit")
        try:
            with open(resolved, "rb") as handle:
                raw = handle.read(self._max_bytes + 1)
        except OSError:
            raise SecretResolutionError(
                f"secret {ref.id!s} could not be read"
            ) from None
        if len(raw) > self._max_bytes:
            raise SecretResolutionError(f"secret {ref.id!s} exceeds the size limit")
        if b"\x00" in raw:
            raise SecretResolutionError(f"secret {ref.id!s} contains an invalid byte")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise SecretResolutionError(
                f"secret {ref.id!s} is not valid UTF-8 text"
            ) from None
        if text.endswith("\r\n"):
            text = text[:-2]
        elif text.endswith("\n"):
            text = text[:-1]
        if not text:
            raise SecretResolutionError(f"secret {ref.id!s} is empty")
        return text


def build_secret_resolver(settings: Settings) -> SecretResolver:
    """Build the process secret resolver from typed settings.

    The single runtime construction point shared by the API and worker:

    - ``env`` is accepted only outside ``prod`` (it is a dev/test convenience);
    - ``prod`` requires the file backend;
    - ``file`` requires an absolute, usable ``AETHERGATE_SECRET_DIRECTORY``.

    There is no implicit fallback: a file-backend deployment never consults
    ``AETHERGATE_SECRET_*`` and a prod deployment never falls back to env.
    """
    backend = settings.secret_backend
    if backend == "env":
        if settings.app_env == "prod":
            raise SecretResolutionError(
                "the env secret backend is not permitted in production; "
                "configure AETHERGATE_SECRET_BACKEND=file"
            )
        return EnvSecretResolver()
    if backend == "file":
        directory = settings.secret_directory
        if not directory:
            raise SecretResolutionError(
                "secret_backend=file requires AETHERGATE_SECRET_DIRECTORY"
            )
        try:
            return FileSecretResolver(directory)
        except ValueError as exc:
            raise SecretResolutionError(
                f"invalid secret directory configuration: {exc}"
            ) from exc
    raise SecretResolutionError(f"unknown secret backend {backend!r}")
