from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from backend.config import settings
from backend.db import (
    check_db_health,
    get_session_messages,
    get_session_system_prompt,
    init_db,
    save_message,
    session_exists,
    set_session_system_prompt,
)
from backend.llm_gateway import (
    DEFAULT_SYSTEM_PROMPT,
    ProviderUnavailableError,
    call_llm_with_failover,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # On startup: initialize database table
    init_db()
    yield


app = FastAPI(
    title="ResilioChat Backend",
    version="1.0.0",
    lifespan=lifespan,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request & Response Models ---

class HealthResponse(BaseModel):
    status: str
    keys_configured: int
    cache_enabled: bool


class MessageItem(BaseModel):
    role: str
    content: str
    metadata: Optional[Dict[str, Any]] = None


class SessionMessagesResponse(BaseModel):
    session_id: str
    messages: List[Dict[str, Any]]


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    message: str = Field(...)
    system_prompt: Optional[str] = None

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        trimmed = v.strip()
        if len(trimmed) < 1 or len(trimmed) > 4000:
            raise ValueError(
                "message must be between 1 and 4000 characters after trimming whitespace"
            )
        return trimmed


class ChatMetadata(BaseModel):
    resolution_source: str
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    key_id: Optional[str] = None
    fallback_attempts: int


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    metadata: ChatMetadata


# --- Endpoints ---

@app.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    """
    Verify DB is reachable and key pool is loaded.
    """
    db_ok = check_db_health()
    if not db_ok:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database connection failure",
        )

    keys_count = len(settings.LLM_API_KEYS)
    return HealthResponse(
        status="ok",
        keys_configured=keys_count,
        cache_enabled=True,
    )


@app.get("/sessions/{session_id}/messages", response_model=SessionMessagesResponse)
def get_session_history(session_id: str) -> SessionMessagesResponse:
    """
    Return stored messages for that session oldest-first, excluding system prompts.
    If session_id has no messages in SQLite, returns 404 with {"detail": "session not found"}.
    """
    if not session_exists(session_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found",
        )

    messages = get_session_messages(session_id, exclude_system=True)
    return SessionMessagesResponse(
        session_id=session_id,
        messages=messages,
    )


@app.post("/chat", response_model=ChatResponse)
def post_chat(
    request: ChatRequest,
    x_simulate_failure: Optional[str] = Header(None, alias="X-Simulate-Failure"),
) -> ChatResponse:
    """
    Processes chat requests with:
      - Multi-turn SQLite persistence
      - LiteLLM caching & key failover
      - Simulated failure testing via X-Simulate-Failure header
    """
    # 1. Resolve system prompt for this session
    if request.system_prompt is not None and request.system_prompt.strip():
        active_system_prompt = request.system_prompt.strip()
        set_session_system_prompt(request.session_id, active_system_prompt)
    else:
        existing_system = get_session_system_prompt(request.session_id)
        active_system_prompt = existing_system if existing_system else DEFAULT_SYSTEM_PROMPT

    # 2. Build full conversation history for LLM
    history = get_session_messages(request.session_id, exclude_system=True)
    llm_messages: List[Dict[str, str]] = [
        {"role": "system", "content": active_system_prompt}
    ]
    for msg in history:
        llm_messages.append({"role": msg["role"], "content": msg["content"]})
    llm_messages.append({"role": "user", "content": request.message})

    # 3. Call LLM Gateway with key failover
    try:
        reply, metadata = call_llm_with_failover(
            messages=llm_messages,
            simulate_failure_header=x_simulate_failure,
        )
    except ProviderUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="all providers unavailable",
        )

    # 4. Save to SQLite only upon successful response
    save_message(
        session_id=request.session_id,
        role="user",
        content=request.message,
        metadata=None,
    )
    save_message(
        session_id=request.session_id,
        role="assistant",
        content=reply,
        metadata=metadata,
    )

    return ChatResponse(
        session_id=request.session_id,
        reply=reply,
        metadata=ChatMetadata(**metadata),
    )
