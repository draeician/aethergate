import litellm
import os
import asyncio
from litellm import acompletion

# 1. Force Verbose Logging (Shows URL and Payload)
litellm.set_verbose = True

# 2. Hardcode the Target (Bypass .env for this test)
# Ensure this matches your network setup.
OLLAMA_API_BASE = "http://nomnom:11434"
MODEL = "ollama/qwen2.5:3b"

async def test_connection():
    print(f"--> Connecting to: {OLLAMA_API_BASE}")
    print(f"--> Requesting Model: {MODEL}")
    
    try:
        response = await acompletion(
            model=MODEL,
            messages=[{"role": "user", "content": "Ping."}],
            api_base=OLLAMA_API_BASE,
            stream=False
        )
        print("\n--> SUCCESS!")
        print(response.choices[0].message.content)
        
    except Exception as e:
        print("\n--> FAILURE")
        print(f"Error Type: {type(e).__name__}")
        print(f"Error Details: {e}")

if __name__ == "__main__":
    asyncio.run(test_connection())
