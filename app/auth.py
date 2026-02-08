from fastapi import Security, HTTPException, Depends
from fastapi.security.api_key import APIKeyHeader
from sqlmodel import select
from sqlalchemy.orm import selectinload
from sqlmodel.ext.asyncio.session import AsyncSession
from app.database import get_session
from app.models import APIKey, User
import hashlib

api_key_header = APIKeyHeader(name="Authorization", auto_error=False)

async def get_current_key(
    api_key_header: str = Security(api_key_header),
    session: AsyncSession = Depends(get_session)
) -> APIKey:
    """
    Validates the Bearer Token and loads the User.
    """
    if not api_key_header:
        raise HTTPException(status_code=401, detail="Missing API Key")

    try:
        token = api_key_header.split(" ")[1]
    except IndexError:
        raise HTTPException(status_code=401, detail="Invalid API Key format")

    # In Production: Hash logic here. For this prototype, we used raw equality in manage.py?
    # Wait, manage.py did: key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    # So we MUST hash the incoming token to find it.
    
    hashed_token = hashlib.sha256(token.encode()).hexdigest()

    # EAGER LOAD USER: options(selectinload(APIKey.user))
    statement = select(APIKey).where(APIKey.key_hash == hashed_token).options(selectinload(APIKey.user))
    result = await session.exec(statement)
    api_key = result.first()

    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid API Key")

    if not api_key.is_active:
        raise HTTPException(status_code=403, detail="API Key is inactive")
        
    if not api_key.user.is_active:
        raise HTTPException(status_code=403, detail="User account is inactive")

    return api_key
