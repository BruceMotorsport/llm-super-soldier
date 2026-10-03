"""
OpenCode / OpenRouter free-tier adapter for Super-Soldier.

WHY THIS EXISTS
---------------
The Provider Console (:9001) shells out to the `opencode` CLI for every
`opencode/*` model. Measured 2026-10-04: all 6 of them fail after ~30s with
"503: All providers failed or stalled", and the CLI itself hangs. Root cause
is credentials, not network:

    ~/.local/share/opencode/auth.json holds ONLY an OpenRouter key.
    There is no OpenCode account credential, so every opencode/* model
    is unauthenticated and can never serve.

Rather than wait on that, this adapter calls the free OpenRouter models
DIRECTLY over HTTPS using the credential we already have. Verified working:

    qwen/qwen3.8-27b:free          1.7s  -> "Tokyo"
    nvidia/nemotron-3.5-lightning:free  2.1s -> answers
    liquid/lfm-2.5-2.6b:free      1.1s  -> answers (reasoning model)

This becomes Server 2 of the Bruce AI architecture: a second independent
source of free capacity alongside Groq, so a Groq rate-limit no longer
stalls Super-Soldier.

Note on reasoning models: `openrouter/*` reasoning models can return
content=None when max_tokens is small, because the whole budget is consumed
by the `reasoning` field. We request a generous budget and fall back to
reasoning text if content is empty — never return an empty answer.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"
AUTH_PATH = os.path.join(
    os.path.expanduser("~"), ".local", "share", "opencode", "auth.json"
)
CACHE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "openrouter_free_models.json"
)
TIMEOUT = 45

# Free models verified to answer. Refreshed from /models when reachable.
# Live sweep 2026-10-04: 12 of 17 free models answered correctly.
# Re-verify with test_openrouter_models.py before trusting this list.
VERIFIED_FREE = [
    "qwen/qwen3.8-27b:free",                    # best all-round reasoning
    "cohere/north-mini-code:free",              # code
    "poolside/laguna-s-2.1:free",               # code (bigger)
    "nvidia/nemotron-3.5-lightning:free",       # fast
    "nvidia/nemotron-3-super-120b-a12b:free",   # large MoE
    "nvidia/nemotron-3-ultra-550b-a55b:free",   # very large MoE
    "inclusionai/ling-3.0-flash-sante:free",
    "liquid/lfm-2.5-2.6b:free",
    "poolside/laguna-xs-2.1:free",
    "apodex/apodex-1.1-mini:free",
    "dots-studio/dots-3-note-preview:free",
]
# EXCLUDED after the sweep:
#   google/gemma-4-*:free            -> 429 (rate-limited)
#   thinkingmachines/inkkin*:free    -> 403 (blocked)
#   nvidia/nemotron-3-nano-omni-*    -> returns a body with no choices

_lock = threading.Lock()
_models_cache: List[str] = []
_models_fetched_at: float = 0.0
_key_cache: Optional[str] = None


def load_key() -> Optional[str]:
    """Read the OpenRouter key from opencode's auth.json. Never logged."""
    global _key_cache
    with _lock:
        if _key_cache:
            return _key_cache
    if not os.path.isfile(AUTH_PATH):
        return None
    try:
        with open(AUTH_PATH, encoding="utf-8") as f:
            data = json.load(f)
        key = (data.get("openrouter") or {}).get("key")
        if key:
            with _lock:
                _key_cache = key
            return key
    except (OSError, ValueError):
        pass
    return None


def fetch_free_models(force: bool = False) -> List[str]:
    """Live list of `:free` models. Cached; falls back to disk/verified."""
    global _models_cache, _models_fetched_at
    now = time.time()
    with _lock:
        if _models_cache and not force and now - _models_fetched_at < 3600:
            return list(_models_cache)

    key = load_key()
    if key:
        try:
            import requests
            r = requests.get(MODELS_URL, headers={"Authorization": f"Bearer {key}"}, timeout=20)
            if r.status_code == 200:
                free = sorted(m["id"] for m in r.json().get("data", [])
                              if m.get("id", "").endswith(":free"))
                if free:
                    with _lock:
                        _models_cache = free
                        _models_fetched_at = now
                    try:
                        with open(CACHE_PATH, "w", encoding="utf-8") as f:
                            json.dump({"fetched": now, "models": free}, f)
                    except OSError:
                        pass
                    return list(free)
        except Exception:
            pass

    # offline fallback: last known on disk
    if os.path.isfile(CACHE_PATH):
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                cached = json.load(f).get("models", [])
            if cached:
                with _lock:
                    _models_cache = cached
                    _models_fetched_at = now
                return list(cached)
        except (OSError, ValueError):
            pass

    with _lock:
        _models_cache = list(VERIFIED_FREE)
        _models_fetched_at = now
    return list(VERIFIED_FREE)


def pick_model(task: str = "") -> str:
    """Choose a free model. Prefer fast ones for short tasks."""
    free = fetch_free_models()
    if not free:
        return VERIFIED_FREE[0]
    t = (task or "").lower()
    if any(k in t for k in ["code", "function", "python", "debug", "script"]):
        for pref in ("cohere/north-mini-code:free", "poolside/laguna-s-2.1:free",
                     "qwen/qwen3.8-27b:free"):
            if pref in free:
                return pref
    for pref in ("qwen/qwen3.8-27b:free", "nvidia/nemotron-3.5-lightning:free"):
        if pref in free:
            return pref
    return free[0]


def chat(prompt: str, model: str = "", max_tokens: int = 2048,
         timeout: int = TIMEOUT) -> Tuple[Optional[str], str]:
    """Call a free OpenRouter model.

    Returns (answer, served_by). `served_by` is the model actually used, or
    an error string. Returns (None, reason) rather than an empty string so
    the caller can fail over honestly.
    """
    key = load_key()
    if not key:
        return None, "no OpenRouter credential"
    mdl = model or pick_model(prompt)
    try:
        import requests
        r = requests.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json",
                     "HTTP-Referer": "http://localhost:8082",
                     "X-Title": "Super-Soldier"},
            json={"model": mdl,
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": max_tokens, "temperature": 0.3},
            timeout=timeout,
        )
        if r.status_code != 200:
            return None, f"http {r.status_code}"
        try:
            payload = r.json()
            choices = payload.get("choices") or []
            if not choices:
                # Some free models return a body with no choices (or an error
                # object under a different key). Fail cleanly, never raise.
                return None, "no choices in response"
            msg = choices[0].get("message") or {}
        except (ValueError, KeyError, IndexError, TypeError) as e:
            return None, f"malformed response ({type(e).__name__})"

        # Reasoning models can spend the whole budget on `reasoning` and
        # return content=None. Use reasoning text rather than nothing.
        text = (msg.get("content") or msg.get("reasoning") or "").strip()
        if not text:
            return None, "empty completion"
        return text[:2000], mdl
    except Exception as e:
        return None, f"{type(e).__name__}"


def health() -> Dict[str, Any]:
    key = load_key()
    models = fetch_free_models()
    return {
        "credential": "present" if key else "MISSING",
        "auth_path": AUTH_PATH,
        "free_models": len(models),
        "models": models[:20],
        "default": pick_model(),
    }


if __name__ == "__main__":
    import json as _j
    print(_j.dumps(health(), indent=2))
    for q in ["What is the capital of Japan?",
              "Write a one-line python function to reverse a string."]:
        ans, by = chat(q)
        print(f"\nQ: {q[:50]}\n  via {by}\n  -> {(ans or '')[:120]}")