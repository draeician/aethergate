from fastapi import FastAPI
from app.routers import proxy, admin
import uvicorn
import os
from dotenv import load_dotenv

# 1. Load Environment Variables immediately
load_dotenv()

app = FastAPI(
    title="AetherGate",
    description="High-Performance LLM Gateway",
    version="0.1.0"
)

# Mount Routers
app.include_router(proxy.router)
app.include_router(admin.router)

@app.get("/health")
async def health_check():
    ollama_url = os.getenv("OLLAMA_API_BASE", "NOT_SET")
    return {
        "status": "online", 
        "system": "AetherGate", 
        "target_inference_engine": ollama_url
    }

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
