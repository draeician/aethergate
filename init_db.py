import asyncio
from app.database import init_db

if __name__ == "__main__":
    print("--> Initializing AetherGate Database...")
    try:
        asyncio.run(init_db())
        print("--> Tables created successfully.")
    except Exception as e:
        print(f"!!! Error creating database: {e}")
