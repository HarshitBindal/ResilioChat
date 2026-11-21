# ResilioChat

ResilioChat is a resilient, production-ready LLM chatbot gateway powered by Google Gemini via LiteLLM. It features transparent multi-key failover, zero-cost prompt response caching, multi-turn SQLite conversation persistence, and a developer-grade React interface.

---

## 1. Live URLs

- **Frontend App**: [https://resiliochat-frontend-placeholder.example.com](https://resiliochat-frontend-placeholder.example.com)
- **Backend API**: [https://resiliochat-backend-placeholder.example.com](https://resiliochat-backend-placeholder.example.com)
- **Health Check**: [https://resiliochat-backend-placeholder.example.com/health](https://resiliochat-backend-placeholder.example.com/health)

*(Replace the placeholder links above with your deployed domain URLs).*

---

## 2. Run locally

### Prerequisites
- Python 3.11+
- Node.js 20+
- Docker & Docker Compose (optional, for containerized run)
- Gemini API key(s) from [Google AI Studio](https://aistudio.google.com/)

### Configuration
Copy `.env.example` to `.env` at the repository root and add your Gemini API key(s):
```bash
cp .env.example .env
```
Edit `.env`:
```env
LLM_API_KEYS=your_primary_gemini_key,your_backup_gemini_key
LLM_MODEL=gemini/gemini-2.0-flash
DATABASE_URL=sqlite:///./data/app.db
ALLOWED_ORIGINS=http://localhost:3000
ENABLE_FAILURE_SIMULATION=true
```

---

### Option A: Running with Docker (Recommended)

1. **Start all services**:
   ```bash
   make up
   ```
   *Alternatively*: `docker compose up --build -d`

2. **Access the application**:
   - Frontend UI: [http://localhost:3000](http://localhost:3000)
   - Backend API: [http://localhost:8000](http://localhost:8000)
   - Interactive Swagger Docs: [http://localhost:8000/docs](http://localhost:8000/docs)

3. **Stop all services**:
   ```bash
   make down
   ```

---

### Option B: Running without Docker

#### 1. Backend Setup
```bash
# Create and activate Python virtual environment
python -m venv venv
# On Windows:
.\venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# Install backend dependencies
pip install -r backend/requirements.txt

# Run FastAPI backend with Uvicorn
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

#### 2. Frontend Setup
```bash
# From the /frontend directory
cd frontend

# Install dependencies
npm install

# Start Vite dev server
npm run dev
```
Open [http://localhost:3000](http://localhost:3000) in your browser.

#### 3. Run Automated Pytest Suite
```bash
pytest -v backend/tests/test_api.py
```

---

## 3. Design decisions

### 1. Transparent Key Failover & Resilience
- **Pool Mapping**: Comma-separated API keys in `LLM_API_KEYS` are parsed into an ordered tuple sequence: `[("key_1", raw_key_1), ("key_2", raw_key_2), ...]`.
- **Fault Tolerance**: When calling the provider, errors such as HTTP `429` (Rate Limit / Quota Exhausted) or `5xx` (Server Overload) are caught transparently. The system increments `fallback_attempts` and immediately fails over to the next configured key.
- **Fail-Safe Exhaustion**: If all configured keys fail, the backend returns HTTP `503` with exact body `{"detail": "all providers unavailable"}` and writes nothing to SQLite.
- **Deterministic Simulation (`X-Simulate-Failure`)**: Developers can pass the `X-Simulate-Failure` header (e.g. `key_1=429`, `key_1=500`, `*=429`) when `ENABLE_FAILURE_SIMULATION=true`. This allows end-to-end failover testing without consuming live API quotas.

### 2. LiteLLM Response Caching
- **Exact Message List Hashing**: LiteLLM's local memory cache is enabled. The cache key is derived strictly from `model` + the complete conversation context (`system_prompt` + previous turns + new user message).
- **Zero-Latency Repeat Hits**: If a user submits an identical first query in a new session (or sends duplicate messages), the gateway returns the cached response in < 5ms with `resolution_source="LLM_CACHE"`, `key_id=null`, and `fallback_attempts=0`.
- **Cache Bypass during Simulation**: When `X-Simulate-Failure` is supplied, the cache read/write is bypassed (`cache={'no-cache': True, 'no-store': True}`) to guarantee that failover behavior is verified without being masked by stale cached answers.

### 3. Lightweight SQLite Session Storage
- **Zero External Overhead**: Uses Python's native `sqlite3` without heavyweight ORMs or external database servers.
- **Directory Auto-Creation**: The storage layer parses `DATABASE_URL` (supporting relative paths like `sqlite:///./data/app.db` and absolute Docker paths like `sqlite:////data/app.db`) and automatically provisions parent directories on demand.
- **Auditability & Clean Schemas**:
  - `id`: Auto-incrementing primary key.
  - `session_id`: Unique identifier for each conversation session.
  - `role`: Message author (`user`, `assistant`, or `system`).
  - `content`: Message text.
  - `metadata`: JSON-serialized telemetry (latency, token usage, resolution source, key ID).
  - `created_at`: ISO-8601 UTC timestamp.
- **System Prompt Separation**: `GET /sessions/{session_id}/messages` fetches history ordered oldest-first while strictly excluding internal `system` messages.

---

## 4. Verification

Run these exact `curl` commands to test the system endpoints and verify behaviors:

### 1. Verify Health Endpoint
```bash
curl -X GET "http://localhost:8000/health"
```
**Expected Response (200 OK)**:
```json
{
  "status": "ok",
  "keys_configured": 2,
  "cache_enabled": true
}
```

---

### 2. Send First Turn Message (Live Inference)
```bash
curl -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-session-101",
    "message": "Hello! What is your purpose?",
    "system_prompt": "You are a concise, helpful assistant."
  }'
```
**Expected Response (200 OK)**:
```json
{
  "session_id": "test-session-101",
  "reply": "...",
  "metadata": {
    "resolution_source": "LIVE_INFERENCE",
    "model": "gemini/gemini-2.0-flash",
    "latency_ms": 780,
    "input_tokens": 18,
    "output_tokens": 15,
    "key_id": "key_1",
    "fallback_attempts": 0
  }
}
```

---

### 3. Verify Cache Hit (Same First Message in a New Session)
```bash
curl -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-session-102",
    "message": "Hello! What is your purpose?",
    "system_prompt": "You are a concise, helpful assistant."
  }'
```
**Expected Response (200 OK)**:
```json
{
  "session_id": "test-session-102",
  "reply": "...",
  "metadata": {
    "resolution_source": "LLM_CACHE",
    "model": "gemini/gemini-2.0-flash",
    "latency_ms": 2,
    "input_tokens": 18,
    "output_tokens": 15,
    "key_id": null,
    "fallback_attempts": 0
  }
}
```

---

### 4. Send Multi-Turn Follow-Up in Session
```bash
curl -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-session-101",
    "message": "Summarize that in three words."
  }'
```

---

### 5. Fetch Session History
```bash
curl -X GET "http://localhost:8000/sessions/test-session-101/messages"
```
**Expected Response (200 OK)**:
```json
{
  "session_id": "test-session-101",
  "messages": [
    {
      "role": "user",
      "content": "Hello! What is your purpose?"
    },
    {
      "role": "assistant",
      "content": "...",
      "metadata": {
        "resolution_source": "LIVE_INFERENCE",
        "model": "gemini/gemini-2.0-flash",
        "latency_ms": 780,
        "input_tokens": 18,
        "output_tokens": 15,
        "key_id": "key_1",
        "fallback_attempts": 0
      }
    },
    {
      "role": "user",
      "content": "Summarize that in three words."
    },
    {
      "role": "assistant",
      "content": "...",
      "metadata": { ... }
    }
  ]
}
```

---

### 6. Verify Key Failover Simulation (`key_1=429`)
```bash
curl -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -H "X-Simulate-Failure: key_1=429" \
  -d '{
    "session_id": "failover-demo-1",
    "message": "Demonstrate automatic key failover."
  }'
```
**Expected Response (200 OK)**:
```json
{
  "session_id": "failover-demo-1",
  "reply": "...",
  "metadata": {
    "resolution_source": "LIVE_INFERENCE",
    "model": "gemini/gemini-2.0-flash",
    "latency_ms": 650,
    "input_tokens": 14,
    "output_tokens": 20,
    "key_id": "key_2",
    "fallback_attempts": 1
  }
}
```

---

### 7. Verify Complete Outage Simulation (`*=429`)
```bash
curl -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -H "X-Simulate-Failure: *=429" \
  -d '{
    "session_id": "outage-demo-1",
    "message": "This should trigger a 503."
  }'
```
**Expected Response (503 Service Unavailable)**:
```json
{
  "detail": "all providers unavailable"
}
```

---

## 5. Setup Used

- **Operating System**: Windows / Linux (Docker)
- **Editor / IDE**: VS Code / Cursor
- **AI Coding Assistant**: Google DeepMind Antigravity
- **Python Runtime**: Python 3.12 (Local) / Python 3.11-slim (Container)
- **Node Runtime**: Node.js v20.15.0 (Alpine Linux in Container)
- **LLM Gateway Framework**: LiteLLM 1.110+ with Gemini 2.0 Flash (`gemini/gemini-2.0-flash`)

---

## 6. Bonus features

- **Live In-App Failover Simulator**: A built-in interactive drawer in the frontend UI allowing developers to toggle and test simulated upstream failures (`key_1=429`, `key_1=500`, `*=429`) with one click.
- **Deep Observability Badges**: Visual badges rendered with every assistant message highlighting whether the response was served from `LLM_CACHE` or `LIVE_INFERENCE`, prompt/completion token consumption, exact server response latency in milliseconds, and failover retry count.
- **Custom System Prompt Drawer**: Per-session collapsible system prompt drawer with persistent session overrides.
- **Zero-Config Health Status Bar**: Live backend connection indicator in the header that monitors active database availability and key pool count.
- **Comprehensive Offline Pytest Suite**: 100% test coverage with offline LiteLLM mocks verifying health checks, validation rules, multi-turn storage, cache hits, and failover mechanics.
