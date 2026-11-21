import os
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
import litellm
from litellm.caching.caching import Cache
from litellm.types.utils import Choices, Message, ModelResponse, Usage

from backend.config import settings
from backend.db import init_db, session_exists
from backend.main import app


def make_mock_response(content: str = "Mocked LLM reply", prompt_tokens: int = 42, completion_tokens: int = 9):
    """Generates a mock ModelResponse matching LiteLLM format."""
    return ModelResponse(
        id="mock-response-id",
        choices=[Choices(message=Message(role="assistant", content=content))],
        usage=Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


@pytest.fixture(autouse=True)
def setup_test_environment(tmp_path, monkeypatch):
    """
    Configures temporary SQLite database, test API keys,
    failure simulation toggle, and a fresh local cache for each test.
    """
    db_file = tmp_path / "test_api.db"
    db_url = f"sqlite:///{db_file}"

    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("LLM_API_KEYS", "test_key_1,test_key_2")
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "true")

    # Initialize SQLite table for the temporary database
    init_db(str(db_file))

    # Reset LiteLLM cache for each test
    litellm.cache = Cache(type="local")

    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


# ==============================================================================
# Case 1: GET /health
# ==============================================================================
def test_health_endpoint(client):
    """
    1. GET /health returns 200 with status='ok', keys_configured >= 2, cache_enabled=true.
    """
    response = client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "ok"
    assert data["keys_configured"] >= 2
    assert data["cache_enabled"] is True


# ==============================================================================
# Case 2: POST /chat validation
# ==============================================================================
def test_chat_validation(client):
    """
    2. POST /chat validation: empty message, whitespace-only message,
       >4000 chars, and missing session_id all return 422 and do not write to SQLite.
    """
    # Empty message
    resp_empty = client.post("/chat", json={"session_id": "test_sess_val", "message": ""})
    assert resp_empty.status_code == 422

    # Whitespace-only message
    resp_whitespace = client.post("/chat", json={"session_id": "test_sess_val", "message": "     "})
    assert resp_whitespace.status_code == 422

    # Over 4000 chars
    resp_too_long = client.post(
        "/chat",
        json={"session_id": "test_sess_val", "message": "a" * 4001},
    )
    assert resp_too_long.status_code == 422

    # Missing session_id
    resp_missing_session = client.post("/chat", json={"message": "Valid hello"})
    assert resp_missing_session.status_code == 422

    # Verify no records were written to SQLite for test_sess_val
    assert not session_exists("test_sess_val")


# ==============================================================================
# Case 3: Multi-turn persistence & session messages
# ==============================================================================
def test_multi_turn_persistence(client):
    """
    3. Multi-turn persistence: send 2 messages in the same session_id,
       verify GET /sessions/{session_id}/messages returns all 4 entries oldest-first
       without the system prompt. Unknown session returns 404 with {"detail": "session not found"}.
    """
    session_id = "test_sess_multiturn"

    def mock_completion(*args, **kwargs):
        user_message = kwargs["messages"][-1]["content"]
        return make_mock_response(content=f"Reply to {user_message}")

    with patch("litellm.completion", side_effect=mock_completion):
        # First turn
        resp1 = client.post(
            "/chat",
            json={
                "session_id": session_id,
                "message": "First message",
                "system_prompt": "You are a custom assistant.",
            },
        )
        assert resp1.status_code == 200

        # Second turn in the same session
        resp2 = client.post(
            "/chat",
            json={
                "session_id": session_id,
                "message": "Second message",
            },
        )
        assert resp2.status_code == 200

    # Verify GET /sessions/{session_id}/messages
    history_resp = client.get(f"/sessions/{session_id}/messages")
    assert history_resp.status_code == 200

    history_data = history_resp.json()
    assert history_data["session_id"] == session_id
    messages = history_data["messages"]

    # Verify exactly 4 entries oldest-first without the system prompt
    assert len(messages) == 4
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "First message"

    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "Reply to First message"
    assert "metadata" in messages[1]

    assert messages[2]["role"] == "user"
    assert messages[2]["content"] == "Second message"

    assert messages[3]["role"] == "assistant"
    assert messages[3]["content"] == "Reply to Second message"
    assert "metadata" in messages[3]

    # Verify unknown session returns 404
    unknown_resp = client.get("/sessions/unknown_nonexistent_session/messages")
    assert unknown_resp.status_code == 404
    assert unknown_resp.json() == {"detail": "session not found"}


# ==============================================================================
# Case 4: Caching
# ==============================================================================
def test_caching(client):
    """
    4. Caching: sending the same first message in two different sessions returns
       LIVE_INFERENCE on the first and LLM_CACHE (with key_id=null, fallback_attempts=0,
       identical reply) on the second.
    """
    session_1 = "sess_cache_1"
    session_2 = "sess_cache_2"
    first_msg = "Hello caching test"

    call_count = 0

    def mock_completion(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return make_mock_response(content="Hello! I am a cached reply.")

    with patch("litellm.completion", side_effect=mock_completion):
        # Turn 1: session_1
        resp1 = client.post("/chat", json={"session_id": session_1, "message": first_msg})
        assert resp1.status_code == 200
        d1 = resp1.json()

        assert d1["metadata"]["resolution_source"] == "LIVE_INFERENCE"
        assert d1["metadata"]["key_id"] == "key_1"
        assert d1["metadata"]["fallback_attempts"] == 0
        first_reply = d1["reply"]

        # Turn 2: session_2 with the exact same first message
        resp2 = client.post("/chat", json={"session_id": session_2, "message": first_msg})
        assert resp2.status_code == 200
        d2 = resp2.json()

        assert d2["metadata"]["resolution_source"] == "LLM_CACHE"
        assert d2["metadata"]["key_id"] is None
        assert d2["metadata"]["fallback_attempts"] == 0
        assert d2["reply"] == first_reply

        # Only one live call should have been made to litellm.completion
        assert call_count == 1


# ==============================================================================
# Case 5: Failover with X-Simulate-Failure
# ==============================================================================
def test_failover_with_simulate_failure(client):
    """
    5. Failover with X-Simulate-Failure:
       - key_1=429 succeeds with key_id="key_2" and fallback_attempts=1.
       - key_1=500 succeeds with key_id="key_2" and fallback_attempts=1.
       - *=429 returns 503 with {"detail": "all providers unavailable"} and adds nothing to SQLite.
    """
    with patch("litellm.completion", return_value=make_mock_response(content="Fallback success")):
        # Subcase 5a: key_1=429
        resp_429 = client.post(
            "/chat",
            json={"session_id": "sess_fail_429", "message": "Test failover 429"},
            headers={"X-Simulate-Failure": "key_1=429"},
        )
        assert resp_429.status_code == 200
        d_429 = resp_429.json()
        assert d_429["metadata"]["key_id"] == "key_2"
        assert d_429["metadata"]["fallback_attempts"] == 1
        assert d_429["metadata"]["resolution_source"] == "LIVE_INFERENCE"

        # Subcase 5b: key_1=500
        resp_500 = client.post(
            "/chat",
            json={"session_id": "sess_fail_500", "message": "Test failover 500"},
            headers={"X-Simulate-Failure": "key_1=500"},
        )
        assert resp_500.status_code == 200
        d_500 = resp_500.json()
        assert d_500["metadata"]["key_id"] == "key_2"
        assert d_500["metadata"]["fallback_attempts"] == 1
        assert d_500["metadata"]["resolution_source"] == "LIVE_INFERENCE"

        # Subcase 5c: *=429
        resp_all_fail = client.post(
            "/chat",
            json={"session_id": "sess_all_fail", "message": "Test all fail"},
            headers={"X-Simulate-Failure": "*=429"},
        )
        assert resp_all_fail.status_code == 503
        assert resp_all_fail.json() == {"detail": "all providers unavailable"}
        assert not session_exists("sess_all_fail")
