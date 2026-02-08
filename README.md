# AetherGate v1.0

A self-hosted, OpenAI-compatible AI Gateway designed to monetize local inference. AetherGate sits between your users and your local LLM server (e.g., Ollama), adding Authentication, Billing, Rate Limiting, and Logging.



## Features
* **OpenAI-Compatible Proxy:** Drop-in replacement for `openai.api_base`.
* **User Management:** Create users, manage balances, and issue API keys.
* **Real-Time Billing:** Deducts credit per-token (Input/Output) automatically.
* **Rate Limiting:** Protects your backend with configurable limits (e.g., `60/m`).
* **Admin Dashboard:** Visual "Mission Control" for monitoring revenue and logs.
* **Dockerized:** Single command deployment.

## Quick Start

### 1. Configuration
Create a `.env` file in the root directory:
```bash
SECRET_KEY="your-secret-key"
MASTER_API_KEY="sk-admin-master-key"
DATABASE_URL="sqlite+aiosqlite:///./aethergate.db"
OLLAMA_API_BASE="http://nomnom:11434" # Your backend URL
```

### 2. Run with Docker (Recommended)
```bash
docker compose up -d
```
* **API:** `http://localhost:8000`
* **Dashboard:** `http://localhost:8501`

### 3. Run Manually (Development)
```bash
# Install dependencies
pip install -r requirements.txt

# Initialize DB
python manage.py init

# Start API
uvicorn app.main:app --reload

# Start Dashboard
streamlit run dashboard.py
```

## Administration (CLI)
Manage the system using the `manage.py` utility inside the container or venv.

```bash
# Create a User with $10.00 credit
python manage.py add-user <Username> --balance 10.00

# Generate an API Key
python manage.py gen-key <Username> --name "default"

# Set Price for a Model (e.g., $1.00 per 1M input tokens)
python manage.py add-model "qwen2.5:3b" "ollama/qwen2.5:3b" --price-in 0.000001 --price-out 0.000002

# Check User Balance
python manage.py check-balance <Username>
```

## Administration (HTTP API)
You can also manage the system remotely using the Admin Endpoints.
**Header Required:** `x-admin-key: <MASTER_API_KEY>`

* `GET /admin/users` - List all users.
* `POST /admin/users` - Create a new user.
* `POST /admin/keys` - Generate a new key for a user.
* `POST /admin/models` - Update model pricing.

See `API_REFERENCE.md` for full details.
