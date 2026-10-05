"""Typed, opaque resource identifiers.

Public and admin identifiers are opaque to consumers. The implementation may use
a UUID internally, but contracts must not leak database auto-increment counters
or table names. Each core resource gets a distinct runtime type (a ``str``
subclass) so references cannot be confused with one another or with raw strings.
"""

from __future__ import annotations

from pydantic_core import core_schema


class ResourceId(str):
    """Base class for opaque resource identifiers."""

    __slots__ = ()

    def __new__(cls, value: str) -> ResourceId:
        if not isinstance(value, str):
            raise TypeError(
                f"{cls.__name__} requires a str value, got {type(value).__name__}"
            )
        if not value.strip():
            raise ValueError(f"{cls.__name__} must not be empty")
        return str.__new__(cls, value)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source_type: object, _handler: object
    ) -> core_schema.CoreSchema:
        # Validate as a plain string, then coerce to the concrete ID subclass.
        return core_schema.no_info_after_validator_function(
            cls, core_schema.str_schema()
        )

    def __eq__(self, other: object) -> bool:
        # IDs of different resource kinds are never equal, even with the same value.
        if type(other) is not type(self):
            return False
        return str.__eq__(self, other)

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def __hash__(self) -> int:
        return str.__hash__(self)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str.__repr__(str(self))})"


class ProjectId(ResourceId):
    __slots__ = ()


class PrincipalId(ResourceId):
    __slots__ = ()


class ApiCredentialId(ResourceId):
    __slots__ = ()


class RoleAssignmentId(ResourceId):
    __slots__ = ()


class ProviderId(ResourceId):
    __slots__ = ()


class ProviderAccountId(ResourceId):
    __slots__ = ()


class SecretRefId(ResourceId):
    __slots__ = ()


class EndpointId(ResourceId):
    __slots__ = ()


class QuotaGroupId(ResourceId):
    __slots__ = ()


class QuotaLimitId(ResourceId):
    __slots__ = ()


class ModelAliasId(ResourceId):
    __slots__ = ()


class RouteBindingId(ResourceId):
    __slots__ = ()


class RequestId(ResourceId):
    __slots__ = ()


class ExecutionAttemptId(ResourceId):
    __slots__ = ()


class ReservationId(ResourceId):
    __slots__ = ()


class UsageRecordId(ResourceId):
    __slots__ = ()


class PriceSnapshotId(ResourceId):
    __slots__ = ()


class LedgerEntryId(ResourceId):
    __slots__ = ()


class AuditEventId(ResourceId):
    __slots__ = ()


class PricePolicyId(ResourceId):
    __slots__ = ()


class BudgetPolicyId(ResourceId):
    __slots__ = ()


class BudgetWindowId(ResourceId):
    __slots__ = ()


class BudgetReservationId(ResourceId):
    __slots__ = ()


class ExternalIdentityId(ResourceId):
    __slots__ = ()


class BrowserSessionId(ResourceId):
    __slots__ = ()


class OidcLoginStateId(ResourceId):
    __slots__ = ()


__all__ = [
    "ResourceId",
    "ProjectId",
    "PrincipalId",
    "ApiCredentialId",
    "RoleAssignmentId",
    "ProviderId",
    "ProviderAccountId",
    "SecretRefId",
    "EndpointId",
    "QuotaGroupId",
    "QuotaLimitId",
    "ModelAliasId",
    "RouteBindingId",
    "RequestId",
    "ExecutionAttemptId",
    "ReservationId",
    "UsageRecordId",
    "PriceSnapshotId",
    "LedgerEntryId",
    "AuditEventId",
    "PricePolicyId",
    "BudgetPolicyId",
    "BudgetWindowId",
    "BudgetReservationId",
    "ExternalIdentityId",
    "BrowserSessionId",
    "OidcLoginStateId",
]
