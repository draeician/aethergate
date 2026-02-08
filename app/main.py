from fastapi import FastAPI
from app.routers import proxy
import uvicorn
import os

app = FastAPI(
    title="AetherGate",
    description="High-Performance LLM Gateway",
    version="0.1.0"
)

# Mount the Proxy Router
app.include_router(proxy.router)

@app.get("/health")
async def health_check():
    return {"status": "online", "system": "AetherGate"}

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
