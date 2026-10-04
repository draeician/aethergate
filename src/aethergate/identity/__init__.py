"""Identity phase 1: scoped inference API credentials and durable authorization.

One source of truth for credential authentication and authorization policy,
shared by the API router and the scheduler worker so the two never diverge.
"""

from aethergate.identity import keys, service

__all__ = ["keys", "service"]
