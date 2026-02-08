# AetherGate API Reference

Base URL: `http://localhost:8000`

## 1. Inference (Public)

### Chat Completions
**POST** `/v1/chat/completions`

Compatible with the standard OpenAI Chat API.

**Headers:**
* `Authorization: Bearer sk-...` (Required)
* `Content-Type: application/json`

**Body:**
```json
{
  "model": "qwen2.5:3b",
  "messages": [{"role": "user", "content": "Hello!"}],
  "stream": true
}
```

**Response:**
Standard OpenAI chunked stream or JSON object.
* **401 Unauthorized:** Invalid or missing API Key.
* **402 Payment Required:** User balance is zero or negative.
* **429 Too Many Requests:** Rate limit exceeded.

---

## 2. Management (Admin Only)

**Authentication:**
All Admin endpoints require the header: `x-admin-key: <MASTER_API_KEY>`

### Users

#### List Users
**GET** `/admin/users`
Returns a list of all registered users.

#### Create User
**POST** `/admin/users`
**Body:**
```json
{
  "username": "client_name",
  "balance": 50.00
}
```

### Keys

#### Generate API Key
**POST** `/admin/keys`
Creates a new access key for an existing user.
**Body:**
```json
{
  "username": "client_name",
  "name": "production-app",
  "rate_limit": "60/m"
}
```
**Response:**
```json
{
  "key": "sk-...",       // SAVE THIS! It is shown only once.
  "key_prefix": "sk-..." // Stored for display purposes.
}
```

### Models & Pricing

#### Upsert Model
**POST** `/admin/models`
Map a public model ID to an internal backend model and set the price per token.
**Body:**
```json
{
  "id": "gpt-4-turbo",           // Public Name
  "litellm_name": "ollama/llama3", // Internal Name
  "price_in": 0.00001,
  "price_out": 0.00003
}
```

## 3. System

#### Health Check
**GET** `/health`
Returns system status and backend connection info.
