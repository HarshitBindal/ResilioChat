# Phase 1: Project Scaffold, Config & SQLite Storage

Create the initial project scaffold for a full-stack LLM chatbot with the following root structure:
/backend
/frontend
.env.example

In /backend, initialize a clean Python FastAPI project (with requirements.txt) using FastAPI, Uvicorn, Pydantic, litellm, python-dotenv, and pytest/httpx.

1. Environment variables (add placeholders to .env.example):
- LLM_API_KEYS (comma-separated keys, e.g., key1,key2; mapped in order to key_1, key_2, ...)
- LLM_MODEL (default: gemini/gemini-2.0-flash)
- DATABASE_URL (default: sqlite:///./data/app.db; support sqlite:////data/app.db for Docker)
- ALLOWED_ORIGINS (comma-separated CORS origins, default: http://localhost:3000)
- ENABLE_FAILURE_SIMULATION (default: true)

2. SQLite layer (backend/db.py):
- Use standard sqlite3 (no external DBs, no ORMs needed).
- Parse DATABASE_URL to extract the file path and automatically create parent directories if missing.
- On FastAPI startup, automatically create this table if it doesn't exist:
  CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT,
    created_at TEXT NOT NULL
  );
- Add helper functions to save a message (with ISO-8601 UTC created_at) and fetch all messages for a session_id ordered oldest-first.

3. Endpoints (backend/main.py):
- Configure CORS using ALLOWED_ORIGINS.
- GET /health: Verify DB is reachable and key pool is loaded. Return 200:
  {"status": "ok", "keys_configured": <int>, "cache_enabled": true}
- GET /sessions/{session_id}/messages: Return stored messages for that session oldest-first, excluding system prompts:
  {"session_id": "<id>", "messages": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "...", "metadata": {...}}]}
  If the session_id has no messages in SQLite, return 404 with {"detail": "session not found"}.

Keep the code clean, modular, and strictly compliant with these exact field names.


# Phase 2: LiteLLM Gateway, Caching & X-Simulate-Failure Hook

Now implement the LiteLLM gateway, caching, key failover, and POST /chat endpoint in /backend.

1. Request validation for POST /chat:
- JSON body:
  - session_id: required string, 1-128 chars
  - message: required string, 1-4000 chars after trimming whitespace
  - system_prompt: optional string (if provided, overrides default system prompt for this session)
- Return 422 JSON error on invalid input. Never save failed requests to SQLite.

2. LiteLLM Caching:
- Enable LiteLLM's built-in cache (litellm.cache = Cache(type="local") or "disk").
- The cache key must depend ONLY on the model + full message list (system prompt + conversation history + new user message) so that sending the exact same first message in a new session returns a cache hit, while a different message does not.
- Check LiteLLM's cache hit flag (e.g., response._hidden_params.get("cache_hit")) to set:
  - resolution_source: "LLM_CACHE" on hit, "LIVE_INFERENCE" on live call
  - key_id: null on LLM_CACHE, otherwise "key_1", "key_2", etc. (never the raw API key)
  - fallback_attempts: 0 on LLM_CACHE, otherwise the count of failed keys before success.

3. Key Failover & X-Simulate-Failure Header:
- Parse LLM_API_KEYS into an ordered list: [("key_1", raw_key_1), ("key_2", raw_key_2), ...].
- Read optional header `X-Simulate-Failure` ONLY when ENABLE_FAILURE_SIMULATION=true.
- Format: comma-separated `<key_id>=<status>` pairs (e.g., `key_1=429`, `key_1=429,key_2=500`, `*=429`). Supported statuses: 429, 500, 502, 503. `*` applies to all keys.
- Before calling the LLM for a key: if that key (or `*`) is in the simulated failure map, simulate that failure, increment fallback_attempts, and move to the next key.
- Note: If a response is a cache hit, handle it cleanly—unless X-Simulate-Failure is explicitly forcing failures, in which case ensure failover can be demonstrated cleanly without cache masking the simulated failure (e.g., bypass cache when X-Simulate-Failure header is present).
- On real HTTP 429, quota errors, or 5xx errors from LiteLLM, catch the exception, increment fallback_attempts, and try the next key.
- If all keys fail, return HTTP 503 with exact body: {"detail": "all providers unavailable"} and do NOT write anything to SQLite.

4. Response & Persistence:
- Measure server-side latency_ms as an integer (milliseconds).
- Extract per-request input_tokens and output_tokens from LiteLLM's usage object (never a cumulative total).
- Save both the user message and assistant message (with metadata JSON) to SQLite.
- Return 200:
{
  "session_id": "...",
  "reply": "...",
  "metadata": {
    "resolution_source": "LIVE_INFERENCE",
    "model": "gemini/gemini-2.0-flash",
    "latency_ms": 812,
    "input_tokens": 42,
    "output_tokens": 9,
    "key_id": "key_1",
    "fallback_attempts": 0
  }
}


# Phase 3: Backend Automated Tests (pytest)

Write a pytest test suite in /backend/tests/test_api.py using FastAPI's TestClient and a temporary SQLite database. Mock LiteLLM calls so tests run fast and offline without real API keys.

Cover these exact cases:
1. GET /health returns 200 with status="ok", keys_configured >= 2, cache_enabled=true.
2. POST /chat validation: empty message, whitespace-only message, >4000 chars, and missing session_id all return 422 and do not write to SQLite.
3. Multi-turn persistence: send 2 messages in the same session_id, verify GET /sessions/{session_id}/messages returns all 4 entries oldest-first without the system prompt. Unknown session returns 404 with {"detail": "session not found"}.
4. Caching: sending the same first message in two different sessions returns LIVE_INFERENCE on the first and LLM_CACHE (with key_id=null, fallback_attempts=0, identical reply) on the second.
5. Failover with X-Simulate-Failure:
   - `key_1=429` succeeds with key_id="key_2" and fallback_attempts=1.
   - `key_1=500` succeeds with key_id="key_2" and fallback_attempts=1.
   - `*=429` returns 503 with {"detail": "all providers unavailable"} and adds nothing to SQLite.

Run pytest and fix any issues until all tests pass.


# Phase 4: React Frontend

Build the React frontend in /frontend using Vite + React (clean modern CSS or Tailwind, keep it lightweight).

Requirements:
1. API Base URL: Read from `import.meta.env.VITE_API_BASE_URL` (default to `http://localhost:8000`).
2. Session Management: Generate a fresh `session_id` (e.g., `s-` + short UUID) on every page load. Send only `{ session_id, message, system_prompt? }` to `POST /chat`.
3. Optional System Prompt: Include a collapsible or top-bar input field for an optional system prompt with `data-testid="system-prompt-input"`.
4. Chat UI & Exact `data-testid` Attributes (DO NOT rename any):
   - Input/textarea for user message: `data-testid="chat-input"` (must clear immediately after sending; Enter key sends, Shift+Enter for newline).
   - Send button: `data-testid="send-button"` (disabled while loading).
   - Each assistant reply container: `data-testid="assistant-message"`, containing the reply text AND a clean metadata badge/bar with:
     - `data-testid="meta-source"` -> exact text `LIVE_INFERENCE` or `LLM_CACHE`
     - `data-testid="meta-model"` -> model name
     - `data-testid="meta-latency"` -> e.g. `812 ms`
     - `data-testid="meta-input-tokens"` -> integer input_tokens
     - `data-testid="meta-output-tokens"` -> integer output_tokens
5. Error Handling: If the backend returns 422, 503, or a network error, display a readable error message inside the chat area (never a blank screen or unhandled crash).


# Phase 5: Docker, Makefile & README

Add Dockerization, a Makefile, and the README at the repo root.

1. Multi-stage Dockerfiles:
- /backend/Dockerfile: multi-stage build using python:3.11-slim, small runtime image, runs uvicorn on port 8000.
- /frontend/Dockerfile: multi-stage build (node:20-alpine to build Vite app -> nginx:alpine to serve static files on port 80, mapped to 3000 in compose). Pass VITE_API_BASE_URL as a build arg.

2. docker-compose.yml (at repo root):
- Runs `backend` on http://localhost:8000 and `frontend` on http://localhost:3000.
- Defines a named volume mounted at `/data` in the backend container, with `DATABASE_URL=sqlite:////data/app.db`.
- Loads backend env vars from `.env`.

3. Makefile (at repo root):
- `up`: runs `docker compose up --build -d`
- `down`: runs `docker compose down`

4. README.md (at repo root):
Use these exact 6 headings:
1. Live URLs (put placeholder links I will fill in after deploying)
2. Run locally (both with `make up` and without Docker)
3. Design decisions (explain key fallback, LiteLLM caching, and SQLite session storage)
4. Verification (exact curl commands to test /health, multi-turn /chat, /sessions/{id}/messages, cache hits, and X-Simulate-Failure failover)
5. Setup Used (placeholder for my editor and AI tools)
6. Bonus features (if any)