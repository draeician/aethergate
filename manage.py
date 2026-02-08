import typer
import asyncio
import hashlib
import secrets
from sqlmodel import select
from app.database import get_session, init_db
from app.models import User, APIKey, LLMModel, Capability, BillingUnit

app = typer.Typer()

# ... (Previous create_user, create_key, add_model functions remain here if you kept them) ...
# To save space, I am appending the new command. 
# INSTRUCTION: If you overwrite manage.py, ensure you keep the imports and init_db.
# Ideally, just paste this function into your existing manage.py or overwrite with the full version below.

async def get_balance_logic(username: str):
    async for session in get_session():
        statement = select(User).where(User.username == username)
        results = await session.exec(statement)
        user = results.first()
        if user:
            print(f"User: {user.username}")
            print(f"Balance: ${user.balance:.6f}")
            print(f"Status: {'Active' if user.is_active else 'Suspended'}")
        else:
            print("User not found.")

@app.command()
def check_balance(username: str):
    """Check the current balance of a user."""
    asyncio.run(get_balance_logic(username))

# --- Re-include previous commands for a complete file ---
# (I will provide the FULL file content to avoid any 'partial paste' errors)

async def create_user_logic(username: str, balance: float):
    async for session in get_session():
        user = User(username=username, balance=balance)
        session.add(user)
        await session.commit()
        print(f"User created: {user.username} - Balance: ${user.balance}")

async def create_key_logic(username: str, name: str):
    async for session in get_session():
        statement = select(User).where(User.username == username)
        results = await session.exec(statement)
        user = results.first()
        if not user:
            print("User not found.")
            return
        raw_key = f"sk-{secrets.token_urlsafe(32)}"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        new_key = APIKey(key_hash=key_hash, key_prefix=raw_key[:8], user_id=user.id, name=name)
        session.add(new_key)
        await session.commit()
        print(f"Key: {raw_key}")

async def add_model_logic(name: str, target: str, price_in: float, price_out: float):
    async for session in get_session():
        statement = select(LLMModel).where(LLMModel.id == name)
        results = await session.exec(statement)
        existing = results.first()
        if existing:
            existing.litellm_name = target
            existing.price_in = price_in
            existing.price_out = price_out
            session.add(existing)
        else:
            model = LLMModel(id=name, litellm_name=target, capability=Capability.TEXT, billing_unit=BillingUnit.TOKEN, price_in=price_in, price_out=price_out)
            session.add(model)
        await session.commit()
        print(f"Model '{name}' mapped to '{target}'.")

@app.command()
def init():
    asyncio.run(init_db())

@app.command()
def add_user(username: str, balance: float = 10.00):
    asyncio.run(create_user_logic(username, balance))

@app.command()
def gen_key(username: str, name: str = "default"):
    asyncio.run(create_key_logic(username, name))

@app.command()
def add_model(name: str, target: str, price_in: float = 0.000001, price_out: float = 0.000002):
    asyncio.run(add_model_logic(name, target, price_in, price_out))

if __name__ == "__main__":
    app()
