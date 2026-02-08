"""
AetherGate — Admin API (Master-Key Protected)

All endpoints require the `x-admin-key` header matching MASTER_API_KEY.
"""

import hashlib
import os
import secrets
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import select, func, col
from sqlalchemy.orm import selectinload
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import (
    APIKey, Capability, BillingUnit,
    LLMEndpoint, LLMModel, RequestLog, User,
)

router = APIRouter(prefix="/admin", tags=["Admin"])


# ---------------------------------------------------------------------------
# Security dependency
# ---------------------------------------------------------------------------

async def verify_master_key(x_admin_key: str = Header(...)) -> str:
    expected = os.getenv("MASTER_API_KEY", "sk-admin-master-key")
    if x_admin_key != expected:
        raise HTTPException(status_code=401, detail="Invalid admin key.")
    return x_admin_key


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class CreateUserRequest(BaseModel):
    username: str
    balance: float = 10.00


class CreateKeyRequest(BaseModel):
    username: str
    name: str = "default"
    rate_limit: Optional[str] = None


class UpsertEndpointRequest(BaseModel):
    name: str
    base_url: str
    api_key: Optional[str] = None
    rpm_limit: Optional[int] = None
    day_limit: Optional[int] = None


class UpsertModelRequest(BaseModel):
    id: str
    litellm_name: str
    price_in: float = 0.000001
    price_out: float = 0.000002
    endpoint_id: Optional[int] = None
    rpm_limit: Optional[int] = None
    day_limit: Optional[int] = None


# ---------------------------------------------------------------------------
# User Endpoints
# ---------------------------------------------------------------------------

@router.post("/users")
async def create_user(
    body: CreateUserRequest,
    _: str = Depends(verify_master_key),
    session: AsyncSession = Depends(get_session),
) -> dict:
    existing = (await session.exec(
        select(User).where(User.username == body.username)
    )).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"User '{body.username}' already exists.")

    user = User(username=body.username, balance=body.balance)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return {
        "id": str(user.id), "username": user.username,
        "balance": user.balance, "is_active": user.is_active,
        "created_at": user.created_at.isoformat(),
    }


@router.get("/users")
async def list_users(
    _: str = Depends(verify_master_key),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    users = (await session.exec(select(User).order_by(User.created_at.desc()))).all()
    return [
        {
            "id": str(u.id), "username": u.username, "balance": u.balance,
            "is_active": u.is_active, "organization": u.organization,
            "created_at": u.created_at.isoformat(),
        }
        for u in users
    ]


# ---------------------------------------------------------------------------
# Key Endpoints
# ---------------------------------------------------------------------------

@router.post("/keys")
async def create_key(
    body: CreateKeyRequest,
    _: str = Depends(verify_master_key),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = (await session.exec(
        select(User).where(User.username == body.username)
    )).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{body.username}' not found.")

    raw_key = f"sk-{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    api_key = APIKey(
        key_hash=key_hash, key_prefix=raw_key[:8],
        user_id=user.id, name=body.name,
        rate_limit_model=body.rate_limit,
    )
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)
    return {
        "key": raw_key, "key_prefix": api_key.key_prefix,
        "name": api_key.name, "rate_limit": api_key.rate_limit_model,
        "user": user.username,
    }


@router.get("/keys")
async def list_keys(
    username: Optional[str] = Query(None),
    _: str = Depends(verify_master_key),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    stmt = select(APIKey).options(selectinload(APIKey.user))
    if username:
        stmt = stmt.join(User).where(User.username == username)
    keys = (await session.exec(stmt)).all()
    return [
        {
            "id": str(k.id), "key_prefix": k.key_prefix,
            "name": k.name, "username": k.user.username,
            "is_active": k.is_active, "rate_limit": k.rate_limit_model,
        }
        for k in keys
    ]


# ---------------------------------------------------------------------------
# Endpoint (Provider) CRUD
# ---------------------------------------------------------------------------

@router.get("/endpoints")
async def list_endpoints(
    _: str = Depends(verify_master_key),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    eps = (await session.exec(select(LLMEndpoint))).all()
    return [
        {
            "id": ep.id, "name": ep.name, "base_url": ep.base_url,
            "has_api_key": ep.api_key is not None and len(ep.api_key) > 0,
            "rpm_limit": ep.rpm_limit, "day_limit": ep.day_limit,
            "is_active": ep.is_active,
        }
        for ep in eps
    ]


@router.post("/endpoints")
async def upsert_endpoint(
    body: UpsertEndpointRequest,
    _: str = Depends(verify_master_key),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Create or update an endpoint. Matches by name."""
    existing = (await session.exec(
        select(LLMEndpoint).where(LLMEndpoint.name == body.name)
    )).first()

    if existing:
        existing.base_url = body.base_url
        existing.api_key = body.api_key
        existing.rpm_limit = body.rpm_limit
        existing.day_limit = body.day_limit
        session.add(existing)
        action = "updated"
        ep_id = existing.id
    else:
        ep = LLMEndpoint(
            name=body.name, base_url=body.base_url,
            api_key=body.api_key, rpm_limit=body.rpm_limit,
            day_limit=body.day_limit,
        )
        session.add(ep)
        await session.flush()  # populate ep.id
        action = "created"
        ep_id = ep.id

    await session.commit()
    return {"id": ep_id, "name": body.name, "action": action}


# ---------------------------------------------------------------------------
# Model CRUD
# ---------------------------------------------------------------------------

@router.post("/models")
async def upsert_model(
    body: UpsertModelRequest,
    _: str = Depends(verify_master_key),
    session: AsyncSession = Depends(get_session),
) -> dict:
    # Validate endpoint_id if provided
    if body.endpoint_id is not None:
        ep = (await session.exec(
            select(LLMEndpoint).where(LLMEndpoint.id == body.endpoint_id)
        )).first()
        if not ep:
            raise HTTPException(status_code=404, detail=f"Endpoint ID {body.endpoint_id} not found.")

    existing = (await session.exec(
        select(LLMModel).where(LLMModel.id == body.id)
    )).first()

    if existing:
        existing.litellm_name = body.litellm_name
        existing.price_in = body.price_in
        existing.price_out = body.price_out
        existing.endpoint_id = body.endpoint_id
        existing.rpm_limit = body.rpm_limit
        existing.day_limit = body.day_limit
        session.add(existing)
        action = "updated"
    else:
        model = LLMModel(
            id=body.id, litellm_name=body.litellm_name,
            capability=Capability.TEXT, billing_unit=BillingUnit.TOKEN,
            price_in=body.price_in, price_out=body.price_out,
            endpoint_id=body.endpoint_id,
            rpm_limit=body.rpm_limit, day_limit=body.day_limit,
        )
        session.add(model)
        action = "created"

    await session.commit()
    return {"model": body.id, "litellm_name": body.litellm_name, "action": action}


@router.get("/models")
async def list_models(
    _: str = Depends(verify_master_key),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    models = (await session.exec(
        select(LLMModel).options(selectinload(LLMModel.endpoint))
    )).all()
    return [
        {
            "id": m.id,
            "litellm_name": m.litellm_name,
            "capability": m.capability.value,
            "billing_unit": m.billing_unit.value,
            "price_in": m.price_in,
            "price_out": m.price_out,
            "is_active": m.is_active,
            "endpoint_id": m.endpoint_id,
            "endpoint_name": m.endpoint.name if m.endpoint else None,
            "rpm_limit": m.rpm_limit,
            "day_limit": m.day_limit,
        }
        for m in models
    ]


# ---------------------------------------------------------------------------
# Logs & Stats
# ---------------------------------------------------------------------------

@router.get("/logs")
async def list_logs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    username: Optional[str] = Query(None),
    _: str = Depends(verify_master_key),
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(RequestLog).options(selectinload(RequestLog.user))
    if username:
        stmt = stmt.join(User).where(User.username == username)
    stmt = stmt.order_by(col(RequestLog.timestamp).desc()).offset(offset).limit(limit)
    logs = (await session.exec(stmt)).all()

    count_stmt = select(func.count()).select_from(RequestLog)
    if username:
        count_stmt = count_stmt.join(User).where(User.username == username)
    total = (await session.exec(count_stmt)).one()

    return {
        "total": total, "offset": offset, "limit": limit,
        "items": [
            {
                "id": str(log.id), "username": log.user.username,
                "model_used": log.model_used,
                "input_units": log.input_units, "output_units": log.output_units,
                "total_cost": log.total_cost,
                "timestamp": log.timestamp.isoformat(),
            }
            for log in logs
        ],
    }


@router.get("/stats")
async def get_stats(
    _: str = Depends(verify_master_key),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user_count = (await session.exec(select(func.count()).select_from(User))).one()
    key_count = (await session.exec(select(func.count()).select_from(APIKey))).one()
    model_count = (await session.exec(select(func.count()).select_from(LLMModel))).one()
    endpoint_count = (await session.exec(select(func.count()).select_from(LLMEndpoint))).one()
    total_requests = (await session.exec(select(func.count()).select_from(RequestLog))).one()
    total_revenue = (await session.exec(
        select(func.coalesce(func.sum(RequestLog.total_cost), 0.0))
    )).one()
    total_input = (await session.exec(
        select(func.coalesce(func.sum(RequestLog.input_units), 0.0))
    )).one()
    total_output = (await session.exec(
        select(func.coalesce(func.sum(RequestLog.output_units), 0.0))
    )).one()

    return {
        "users": user_count, "api_keys": key_count,
        "models": model_count, "endpoints": endpoint_count,
        "total_requests": total_requests,
        "total_revenue": float(total_revenue),
        "total_input_tokens": float(total_input),
        "total_output_tokens": float(total_output),
    }


# ---------------------------------------------------------------------------
# Backup & Restore
# ---------------------------------------------------------------------------

BACKUP_VERSION = 1


@router.get("/backup/export")
async def export_backup(
    _: str = Depends(verify_master_key),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Export all configuration data as a portable JSON snapshot."""

    endpoints = (await session.exec(select(LLMEndpoint))).all()
    models = (await session.exec(select(LLMModel))).all()
    users = (await session.exec(select(User))).all()
    keys = (await session.exec(select(APIKey))).all()

    return {
        "version": BACKUP_VERSION,
        "timestamp": datetime.utcnow().isoformat(),
        "data": {
            "endpoints": [
                {
                    "id": ep.id, "name": ep.name, "base_url": ep.base_url,
                    "api_key": ep.api_key, "rpm_limit": ep.rpm_limit,
                    "day_limit": ep.day_limit, "is_active": ep.is_active,
                }
                for ep in endpoints
            ],
            "models": [
                {
                    "id": m.id, "litellm_name": m.litellm_name,
                    "capability": m.capability.value,
                    "billing_unit": m.billing_unit.value,
                    "price_in": m.price_in, "price_out": m.price_out,
                    "is_active": m.is_active,
                    "fallback_model_id": m.fallback_model_id,
                    "endpoint_id": m.endpoint_id,
                    "rpm_limit": m.rpm_limit, "day_limit": m.day_limit,
                }
                for m in models
            ],
            "users": [
                {
                    "id": str(u.id), "username": u.username,
                    "email": u.email, "balance": u.balance,
                    "is_active": u.is_active, "organization": u.organization,
                    "created_at": u.created_at.isoformat() if u.created_at else None,
                }
                for u in users
            ],
            "api_keys": [
                {
                    "id": str(k.id), "key_hash": k.key_hash,
                    "key_prefix": k.key_prefix, "user_id": str(k.user_id),
                    "name": k.name, "is_active": k.is_active,
                    "log_content": k.log_content,
                    "rate_limit_model": k.rate_limit_model,
                    "allowed_capabilities": k.allowed_capabilities,
                }
                for k in keys
            ],
        },
    }


class BackupPayload(BaseModel):
    version: int
    timestamp: str
    data: dict[str, Any]


@router.post("/backup/import")
async def import_backup(
    body: BackupPayload,
    _: str = Depends(verify_master_key),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Restore configuration from a backup snapshot (smart upsert, all-or-nothing)."""

    if body.version != BACKUP_VERSION:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported backup version {body.version} (expected {BACKUP_VERSION}).",
        )

    data = body.data
    counts: dict[str, dict[str, int]] = {
        "endpoints": {"created": 0, "updated": 0},
        "models": {"created": 0, "updated": 0},
        "users": {"created": 0, "updated": 0},
        "api_keys": {"created": 0, "updated": 0},
    }

    # Map old endpoint IDs -> new endpoint IDs (in case auto-increment differs)
    ep_id_map: dict[int, int] = {}

    try:
        # ---- Endpoints ----
        for ep_data in data.get("endpoints", []):
            old_id = ep_data.get("id")
            existing = (await session.exec(
                select(LLMEndpoint).where(LLMEndpoint.name == ep_data["name"])
            )).first()

            if existing:
                existing.base_url = ep_data["base_url"]
                existing.api_key = ep_data.get("api_key")
                existing.rpm_limit = ep_data.get("rpm_limit")
                existing.day_limit = ep_data.get("day_limit")
                existing.is_active = ep_data.get("is_active", True)
                session.add(existing)
                counts["endpoints"]["updated"] += 1
                if old_id is not None:
                    ep_id_map[old_id] = existing.id
            else:
                ep = LLMEndpoint(
                    name=ep_data["name"], base_url=ep_data["base_url"],
                    api_key=ep_data.get("api_key"),
                    rpm_limit=ep_data.get("rpm_limit"),
                    day_limit=ep_data.get("day_limit"),
                    is_active=ep_data.get("is_active", True),
                )
                session.add(ep)
                await session.flush()
                counts["endpoints"]["created"] += 1
                if old_id is not None:
                    ep_id_map[old_id] = ep.id

        # ---- Models (after endpoints so FK mapping is ready) ----
        for m_data in data.get("models", []):
            # Resolve endpoint_id through the mapping
            raw_ep_id = m_data.get("endpoint_id")
            resolved_ep_id = ep_id_map.get(raw_ep_id, raw_ep_id) if raw_ep_id is not None else None

            existing = (await session.exec(
                select(LLMModel).where(LLMModel.id == m_data["id"])
            )).first()

            if existing:
                existing.litellm_name = m_data["litellm_name"]
                existing.capability = Capability(m_data.get("capability", "TEXT"))
                existing.billing_unit = BillingUnit(m_data.get("billing_unit", "TOKEN"))
                existing.price_in = m_data.get("price_in", 0.0)
                existing.price_out = m_data.get("price_out", 0.0)
                existing.is_active = m_data.get("is_active", True)
                existing.fallback_model_id = m_data.get("fallback_model_id")
                existing.endpoint_id = resolved_ep_id
                existing.rpm_limit = m_data.get("rpm_limit")
                existing.day_limit = m_data.get("day_limit")
                session.add(existing)
                counts["models"]["updated"] += 1
            else:
                model = LLMModel(
                    id=m_data["id"], litellm_name=m_data["litellm_name"],
                    capability=Capability(m_data.get("capability", "TEXT")),
                    billing_unit=BillingUnit(m_data.get("billing_unit", "TOKEN")),
                    price_in=m_data.get("price_in", 0.0),
                    price_out=m_data.get("price_out", 0.0),
                    is_active=m_data.get("is_active", True),
                    fallback_model_id=m_data.get("fallback_model_id"),
                    endpoint_id=resolved_ep_id,
                    rpm_limit=m_data.get("rpm_limit"),
                    day_limit=m_data.get("day_limit"),
                )
                session.add(model)
                counts["models"]["created"] += 1

        # ---- Users ----
        for u_data in data.get("users", []):
            user_id = uuid.UUID(u_data["id"])
            existing = (await session.exec(
                select(User).where(User.id == user_id)
            )).first()

            if existing:
                existing.username = u_data["username"]
                existing.email = u_data.get("email")
                existing.balance = u_data.get("balance", 0.0)
                existing.is_active = u_data.get("is_active", True)
                existing.organization = u_data.get("organization")
                session.add(existing)
                counts["users"]["updated"] += 1
            else:
                created_at = (
                    datetime.fromisoformat(u_data["created_at"])
                    if u_data.get("created_at") else datetime.utcnow()
                )
                user = User(
                    id=user_id, username=u_data["username"],
                    email=u_data.get("email"),
                    balance=u_data.get("balance", 0.0),
                    is_active=u_data.get("is_active", True),
                    organization=u_data.get("organization"),
                    created_at=created_at,
                )
                session.add(user)
                counts["users"]["created"] += 1

        # ---- API Keys ----
        for k_data in data.get("api_keys", []):
            key_id = uuid.UUID(k_data["id"])
            existing = (await session.exec(
                select(APIKey).where(APIKey.id == key_id)
            )).first()

            if existing:
                existing.key_hash = k_data["key_hash"]
                existing.key_prefix = k_data["key_prefix"]
                existing.user_id = uuid.UUID(k_data["user_id"])
                existing.name = k_data.get("name", "default")
                existing.is_active = k_data.get("is_active", True)
                existing.log_content = k_data.get("log_content", True)
                existing.rate_limit_model = k_data.get("rate_limit_model")
                existing.allowed_capabilities = k_data.get("allowed_capabilities", [])
                session.add(existing)
                counts["api_keys"]["updated"] += 1
            else:
                api_key = APIKey(
                    id=key_id, key_hash=k_data["key_hash"],
                    key_prefix=k_data["key_prefix"],
                    user_id=uuid.UUID(k_data["user_id"]),
                    name=k_data.get("name", "default"),
                    is_active=k_data.get("is_active", True),
                    log_content=k_data.get("log_content", True),
                    rate_limit_model=k_data.get("rate_limit_model"),
                    allowed_capabilities=k_data.get("allowed_capabilities", []),
                )
                session.add(api_key)
                counts["api_keys"]["created"] += 1

        # All-or-nothing commit
        await session.commit()

    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Import failed — rolled back. Error: {e}")

    return {
        "status": "ok",
        "restored": counts,
    }
