import time
from typing import Any, Dict, List, Optional, Tuple
import litellm
from litellm.caching.caching import Cache

from backend.config import settings

# Default system prompt for sessions
DEFAULT_SYSTEM_PROMPT = "You are a helpful and resilient AI assistant."

# Enable LiteLLM's built-in local cache
if litellm.cache is None:
    litellm.cache = Cache(type="local")


class ProviderUnavailableError(Exception):
    """Raised when all LLM providers/keys fail or are unavailable."""
    pass


def parse_simulate_failure_header(header_value: Optional[str]) -> Dict[str, int]:
    """
    Parses X-Simulate-Failure header into a mapping of key_id -> status_code.
    Example: 'key_1=429,key_2=500' -> {'key_1': 429, 'key_2': 500}
    Supports: 429, 500, 502, 503 and wildcard '*'.
    """
    if not header_value:
        return {}
    failures: Dict[str, int] = {}
    for item in header_value.split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        key_id, status_str = item.split("=", 1)
        key_id = key_id.strip()
        try:
            status_code = int(status_str.strip())
            if status_code in (429, 500, 502, 503):
                failures[key_id] = status_code
        except ValueError:
            continue
    return failures


def call_llm_with_failover(
    messages: List[Dict[str, str]],
    simulate_failure_header: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Executes a chat completion via LiteLLM with:
      - Ordered key pool failover
      - Simulated failure handling via X-Simulate-Failure header
      - LiteLLM caching (bypassed if simulated failure is requested)
      - Exact metadata generation

    Returns:
      (reply_text, metadata_dict)
    """
    start_time = time.time()

    # Parse failure simulation if enabled
    simulated_failures: Dict[str, int] = {}
    if settings.ENABLE_FAILURE_SIMULATION and simulate_failure_header:
        simulated_failures = parse_simulate_failure_header(simulate_failure_header)

    # Bypass cache if failure simulation is active to ensure failover can be demonstrated
    bypass_cache = bool(simulated_failures)

    keys = settings.LLM_API_KEYS
    if not keys:
        raise ProviderUnavailableError("all providers unavailable")

    fallback_attempts = 0
    successful_response: Any = None
    successful_key_id: Optional[str] = None
    is_cache_hit = False
    actual_model_used = settings.LLM_MODEL

    # 1. Check LiteLLM cache if not bypassing
    if not bypass_cache and litellm.cache is not None:
        try:
            cached_result = litellm.cache.get_cache(
                model=settings.LLM_MODEL,
                messages=messages,
            )
            if cached_result is not None:
                successful_response = cached_result
                is_cache_hit = True
        except Exception:
            pass

    # 2. If not a cache hit, iterate through key pool
    if successful_response is None:
        for key_id, raw_key in keys:
            # Check if key is marked for simulated failure
            if key_id in simulated_failures or "*" in simulated_failures:
                fallback_attempts += 1
                continue

            # Attempt real or mocked LiteLLM call
            try:
                cache_opts = {"no-cache": True, "no-store": True} if bypass_cache else None
                extra_kwargs: Dict[str, Any] = {}
                if cache_opts is not None:
                    extra_kwargs["cache"] = cache_opts

                target_model = settings.LLM_MODEL
                try:
                    response = litellm.completion(
                        model=target_model,
                        messages=messages,
                        api_key=raw_key,
                        **extra_kwargs,
                    )
                except Exception as model_err:
                    err_msg = str(model_err).lower()
                    # If Google retired gemini-2.0-flash, automatically upgrade to gemini-3.8-flash
                    if "no longer available" in err_msg or ("404" in err_msg and "gemini-2.0" in target_model):
                        print(f"[LLM Gateway] Model {target_model} retired, falling back to gemini/gemini-3.8-flash", flush=True)
                        target_model = "gemini/gemini-3.8-flash"
                        response = litellm.completion(
                            model=target_model,
                            messages=messages,
                            api_key=raw_key,
                            **extra_kwargs,
                        )
                    else:
                        raise model_err

                successful_response = response
                successful_key_id = key_id
                actual_model_used = target_model

                # Check if LiteLLM internal cache hit occurred
                hidden_params = getattr(response, "_hidden_params", {}) or {}
                if hidden_params.get("cache_hit") is True:
                    is_cache_hit = True
                else:
                    # Save to LiteLLM cache for identical future requests
                    if not bypass_cache and litellm.cache is not None:
                        try:
                            litellm.cache.add_cache(
                                model=settings.LLM_MODEL,
                                messages=messages,
                                result=response,
                            )
                        except Exception:
                            pass
                break
            except Exception as exc:
                # Real HTTP 429, quota errors, authentication, or 5xx errors from LiteLLM
                print(f"[LLM Gateway] Key {key_id} failed: {type(exc).__name__} - {exc}", flush=True)
                fallback_attempts += 1
                continue

    if successful_response is None:
        raise ProviderUnavailableError("all providers unavailable")

    # Measure server-side latency in integer milliseconds
    latency_ms = int((time.time() - start_time) * 1000)

    # Extract choices and reply text (handles ModelResponse object or cached dict)
    reply = ""
    input_tokens = 0
    output_tokens = 0

    if isinstance(successful_response, dict):
        choices = successful_response.get("choices", [])
        if choices and len(choices) > 0:
            first_c = choices[0]
            if isinstance(first_c, dict):
                msg = first_c.get("message", {})
                reply = msg.get("content", "") or ""
            elif hasattr(first_c, "message"):
                reply = getattr(first_c.message, "content", "") or ""
        usage = successful_response.get("usage", {})
        if isinstance(usage, dict):
            input_tokens = int(usage.get("prompt_tokens", 0) or 0)
            output_tokens = int(usage.get("completion_tokens", 0) or 0)
        elif usage is not None:
            input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    else:
        choices = getattr(successful_response, "choices", [])
        if choices and len(choices) > 0:
            first_choice = choices[0]
            if hasattr(first_choice, "message") and hasattr(first_choice.message, "content"):
                reply = first_choice.message.content or ""
            elif isinstance(first_choice, dict) and "message" in first_choice:
                reply = first_choice["message"].get("content", "") or ""
        usage = getattr(successful_response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0

    if is_cache_hit:
        resolution_source = "LLM_CACHE"
        resolved_key_id = None
        resolved_fallback_attempts = 0
    else:
        resolution_source = "LIVE_INFERENCE"
        resolved_key_id = successful_key_id
        resolved_fallback_attempts = fallback_attempts

    metadata: Dict[str, Any] = {
        "resolution_source": resolution_source,
        "model": actual_model_used,
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "key_id": resolved_key_id,
        "fallback_attempts": resolved_fallback_attempts,
    }

    return reply, metadata
