"""
AetherGate — Admin API (Master-Key Protected)

All endpoints require the `x-admin-key` header matching MASTER_API_KEY.
"""

import hashlib
import os
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import APIKey, Capability, BillingUnit, LLMModel, User

router = APIRouter(prefix="/admin", tags=["Admin"])


# ---------------------------------------------------------------------------
# Security dependency
# ---------------------------------------------------------------------------

async def verify_master_key(x_admin_key: str = Header(...)) -> str:
    """Reject any request whose x-admin-key header doesn't match the env var."""
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
    rate_limit: Optional[str] = None  # e.g. "60/m"


class UpsertModelRequest(BaseModel):
    id: str
    litellm_name: str
    price_in: float = 0.000001
    price_out: float = 0.000002


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/users")
async def create_user(
    body: CreateUserRequest,
    _: str = Depends(verify_master_key),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Create a new user with an initial balance."""
    # Check for duplicate username
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
        "id": str(user.id),
        "username": user.username,
        "balance": user.balance,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat(),
    }


@router.get("/users")
async def list_users(
    _: str = Depends(verify_master_key),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """List all registered users."""
    users = (await session.exec(select(User).order_by(User.created_at.desc()))).all()
    return [
        {
            "id": str(u.id),
            "username": u.username,
            "balance": u.balance,
            "is_active": u.is_active,
            "organization": u.organization,
            "created_at": u.created_at.isoformat(),
        }
        for u in users
    ]


@router.post("/keys")
async def create_key(
    body: CreateKeyRequest,
    _: str = Depends(verify_master_key),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Generate a new API key for an existing user. Returns the raw key once."""
    # Resolve user
    user = (await session.exec(
        select(User).where(User.username == body.username)
    )).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{body.username}' not found.")

    # Generate key
    raw_key = f"sk-{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=raw_key[:8],
        user_id=user.id,
        name=body.name,
        rate_limit_model=body.rate_limit,
    )
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    return {
        "key": raw_key,
        "key_prefix": api_key.key_prefix,
        "name": api_key.name,
        "rate_limit": api_key.rate_limit_model,
        "user": user.username,
    }


@router.post("/models")
async def upsert_model(
    body: UpsertModelRequest,
    _: str = Depends(verify_master_key),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Create or update an LLM model mapping and its pricing."""
    existing = (await session.exec(
        select(LLMModel).where(LLMModel.id == body.id)
    )).first()

    if existing:
        existing.litellm_name = body.litellm_name
        existing.price_in = body.price_in
        existing.price_out = body.price_out
        session.add(existing)
        action = "updated"
    else:
        model = LLMModel(
            id=body.id,
            litellm_name=body.litellm_name,
            capability=Capability.TEXT,
            billing_unit=BillingUnit.TOKEN,
            price_in=body.price_in,
            price_out=body.price_out,
        )
        session.add(model)
        action = "created"

    await session.commit()

    return {"model": body.id, "litellm_name": body.litellm_name, "action": action}
