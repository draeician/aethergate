"""CLI profile storage under XDG conventions (``~/.config/aethergate/config.toml``).

Profiles carry no secrets: ``base_url`` and an optional trusted CA bundle path
only. The raw bearer token is never written here — it lives in the protected
:mod:`aethergate.cli.tokenstore`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11 fallback
    import tomli as tomllib  # type: ignore[no-redef]

import tomli_w

from aethergate.cli.errors import NotFoundError, UsageError

APP_DIR_NAME = "aethergate"
CONFIG_FILE_NAME = "config.toml"

# Hosts where plain HTTP is accepted without an explicit --insecure override.
_LOCALHOST_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


@dataclass(frozen=True)
class Profile:
    name: str
    base_url: str
    ca_bundle: str | None = None

    @property
    def verify(self) -> bool | str:
        """Value suitable for httpx's ``verify``: system trust or a CA bundle path."""
        return self.ca_bundle if self.ca_bundle else True


@dataclass
class ProfileStore:
    profiles: dict[str, Profile]
    default_profile: str | None = None


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / APP_DIR_NAME


def config_path() -> Path:
    return config_dir() / CONFIG_FILE_NAME


def _normalize_base_url(base_url: str, *, allow_insecure: bool) -> str:
    base_url = base_url.rstrip("/")
    try:
        parts = urlsplit(base_url)
    except ValueError as exc:
        raise UsageError(f"invalid base URL {base_url!r}") from exc
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise UsageError(f"base URL must be an absolute http(s) URL: {base_url!r}")
    if parts.scheme == "http" and not allow_insecure:
        host = parts.hostname or ""
        if host not in _LOCALHOST_HOSTS:
            raise UsageError(
                f"refusing plain http base URL {base_url!r}; use https or pass --insecure "
                "for a local/test profile"
            )
    return base_url


def load() -> ProfileStore:
    path = config_path()
    if not path.exists():
        return ProfileStore(profiles={})
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise UsageError(f"could not read profile config {path}: {exc}") from exc

    raw_profiles = data.get("profiles", {})
    profiles: dict[str, Profile] = {}
    for name, raw in raw_profiles.items():
        if not isinstance(raw, dict):
            continue
        base_url = raw.get("base_url")
        if not isinstance(base_url, str) or not base_url:
            continue
        ca_bundle = raw.get("ca_bundle")
        profiles[name] = Profile(
            name=name,
            base_url=base_url.rstrip("/"),
            ca_bundle=ca_bundle if isinstance(ca_bundle, str) and ca_bundle else None,
        )
    default = data.get("default_profile")
    return ProfileStore(
        profiles=profiles,
        default_profile=default if isinstance(default, str) else None,
    )


def save(store: ProfileStore) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {
        "version": 1,
        "profiles": {
            name: {
                "base_url": profile.base_url,
                **({"ca_bundle": profile.ca_bundle} if profile.ca_bundle else {}),
            }
            for name, profile in store.profiles.items()
        },
    }
    if store.default_profile:
        data["default_profile"] = store.default_profile
    path.write_text(tomli_w.dumps(data), encoding="utf-8")


def get(store: ProfileStore, name: str | None) -> Profile:
    if name is None:
        name = store.default_profile
    if name is None:
        raise UsageError("no profile selected and no default profile is set")
    profile = store.profiles.get(name)
    if profile is None:
        raise NotFoundError(f"profile {name!r} does not exist")
    return profile


def add(
    store: ProfileStore,
    name: str,
    base_url: str,
    *,
    ca_bundle: str | None,
    allow_insecure: bool,
    set_default: bool,
) -> Profile:
    if not name or name.strip() != name:
        raise UsageError("profile name must be non-empty with no surrounding whitespace")
    normalized = _normalize_base_url(base_url, allow_insecure=allow_insecure)
    profile = Profile(name=name, base_url=normalized, ca_bundle=ca_bundle or None)
    store.profiles[name] = profile
    if set_default or store.default_profile is None:
        store.default_profile = name
    save(store)
    return profile


def delete(store: ProfileStore, name: str) -> None:
    if name not in store.profiles:
        raise NotFoundError(f"profile {name!r} does not exist")
    del store.profiles[name]
    if store.default_profile == name:
        store.default_profile = next(iter(store.profiles), None)
    save(store)


def set_default(store: ProfileStore, name: str) -> None:
    if name not in store.profiles:
        raise NotFoundError(f"profile {name!r} does not exist")
    store.default_profile = name
    save(store)


__all__ = [
    "Profile",
    "ProfileStore",
    "add",
    "config_dir",
    "config_path",
    "delete",
    "get",
    "load",
    "save",
    "set_default",
]
