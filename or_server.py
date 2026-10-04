#!/usr/bin/env python3
"""
Isolated OpenRouter Free Server  (standalone process, port 8085)
================================================================
Author: Buddy | Directive: Bruce — "keep the openrouter system isolated from
the other servers... there are attached parasites that have caused us problems
in the past."

WHAT THIS IS
-------------
A self-contained OpenAI-compatible server for OpenRouter's FREE models.
It runs in its OWN process on its OWN port and shares NO code with
Super-Soldier, Groq, or the OpenCode bridge. Super-Soldier talks to it over
HTTP like any other client — if this server dies, Super-Soldier is unaffected;
if OpenRouter misbehaves, only this process is affected.

It deliberately does NOT import anything from the other servers. The only
shared thing is the OpenRouter credential, which is read from its OWN env var
(`OR_API_KEY`) and falls back to opencode's auth.json — read-only, never
written.

OpenAI-compatible surface:
    GET  /v1/models            list free models
    POST /v1/chat/completions  chat (OpenAI shape)
    GET  /health               liveness
    GET  /stats                usage + which models verified

Run:  python or_server.py            (defaults to 127.0.0.1:8085)
Env:  OR_HOST, OR_PORT, OR_API_KEY, OR_TIMEOUT
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

HOST = os.getenv("OR_HOST", "127.0.0.1")
PORT = int(os.getenv("OR_PORT", "8085"))
TIMEOUT = int(os.getenv("OR_TIMEOUT", "45"))
API_KEY_ENV = os.getenv("OR_API_KEY", "")

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"

# Read-only fallback to opencode's credential store. NEVER written to.
AUTH_FALLBACK = os.path.join(os.path.expanduser("~"), ".local", "share",
                             "opencode", "auth.json")

_lock = threading.Lock()
_key_cache: Optional[str] = None
_model_cache: List[str] = []
_model_fetched: float = 0.0
_stats = {"requests": 0, "served": 0, "failed": 0, "by_model": {}}

# Live sweep 2026-10-04: 12 of 17 free models answered correctly.
VERIFIED = [
    "qwen/qwen3.8-27b:free",
    "cohere/north-mini-code:free",
    "poolside/laguna-s-2.1:free",
    "nvidia/nemotron-3.5-lightning:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "inclusionai/ling-3.0-flash-sante:free",
    "liquid/lfm-2.5-2.6b:free",
    "poolside/laguna-xs-2.1:free",
    "apodex/apodex-1.1-mini:free",
    "dots-studio/dots-3-note-preview:free",
]


def api_key() -> Optional[str]:
    """Our own env var, else read-only fallback. Never logged."""
    global _key_cache
    if API_KEY_ENV:
        return API_KEY_ENV
    with _lock:
        if _key_cache:
            return _key_cache
    try:
        with open(AUTH_FALLBACK, encoding="utf-8") as f:
            k = (json.load(f).get("openrouter") or {}).get("key")
        if k:
            with _lock:
                _key_cache = k
            return k
    except (OSError, ValueError):
        pass
    return None


def free_models(force: bool = False) -> List[str]:
    global _model_cache, _model_fetched
    now = time.time()
    with _lock:
        if _model_cache and not force and now - _model_fetched < 3600:
            return list(_model_cache)
    k = api_key()
    if k:
        try:
            import requests
            r = requests.get(MODELS_URL, headers={"Authorization": f"Bearer {k}"},
                             timeout=20)
            if r.status_code == 200:
                free = sorted(m["id"] for m in r.json().get("data", [])
                              if m.get("id", "").endswith(":free"))
                if free:
                    with _lock:
                        _model_cache, _model_fetched = free, now
                    return list(free)
        except Exception:
            pass
    with _lock:
        _model_cache, _model_fetched = list(VERIFIED), now
    return list(VERIFIED)


def pick(task: str = "") -> str:
    free = free_models()
    t = (task or "").lower()
    if any(k in t for k in ("code", "function", "python", "debug", "script")):
        for p in ("cohere/north-mini-code:free", "poolside/laguna-s-2.1:free"):
            if p in free:
                return p
    for p in ("qwen/qwen3.8-27b:free", "nvidia/nemotron-3.5-lightning:free"):
        if p in free:
            return p
    return free[0] if free else VERIFIED[0]


def complete(messages: List[Dict], model: str = "", max_tokens: int = 2048
             ) -> Tuple[Optional[str], str, Optional[str]]:
    """Returns (content, model_used, error)."""
    k = api_key()
    if not k:
        return None, "", "no OR_API_KEY and no fallback credential"
    prompt = "\n".join(m.get("content") or "" for m in messages
                       if m.get("role") in ("user", "system", "assistant"))
    mdl = model or pick(prompt)
    try:
        import requests
        r = requests.post(
            CHAT_URL,
            headers={"Authorization": f"Bearer {k}",
                     "Content-Type": "application/json",
                     "HTTP-Referer": "http://localhost:8082",
                     "X-Title": "Super-Soldier-OpenRouter"},
            json={"model": mdl, "messages": messages,
                  "max_tokens": max_tokens, "temperature": 0.3},
            timeout=TIMEOUT)
        if r.status_code != 200:
            return None, mdl, f"http {r.status_code}"
        payload = r.json()
        ch = payload.get("choices") or []
        if not ch:
            return None, mdl, "no choices"
        msg = ch[0].get("message") or {}
        # Reasoning models can return content=None when the budget went to
        # `reasoning`. Use it rather than returning nothing.
        text = (msg.get("content") or msg.get("reasoning") or "").strip()
        if not text:
            return None, mdl, "empty completion"
        with _lock:
            _stats["served"] += 1
            _stats["by_model"][mdl] = _stats["by_model"].get(mdl, 0) + 1
        return text, mdl, None
    except Exception as e:
        with _lock:
            _stats["failed"] += 1
        return None, mdl, type(e).__name__


# ── tiny HTTP layer (no framework: keeps this server dependency-free) ──
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, body: Dict[str, Any]):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):        # keep the console readable
        pass

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/health", "/healthz"):
            self._send(200, {"status": "ok", "server": "openrouter-free",
                             "port": PORT, "credential": "present" if api_key() else "MISSING"})
        elif p in ("/v1/models", "/models"):
            with _lock:
                _stats["requests"] += 1
            self._send(200, {"object": "list", "data": [
                {"id": m, "object": "model", "owned_by": "openrouter",
                 "context_window": 32768} for m in free_models()]})
        elif p == "/stats":
            self._send(200, {"status": "ok",
                             "credential": "present" if api_key() else "MISSING",
                             "verified_models": VERIFIED,
                             "free_models": len(free_models()),
                             **(_stats | {})})
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        p = self.path.split("?")[0]
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2 * 1024 * 1024:
            self._send(413, {"error": {"message": "payload too large"}})
            return
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            self._send(400, {"error": {"message": "invalid JSON"}})
            return

        with _lock:
            _stats["requests"] += 1

        if p == "/v1/chat/completions":
            msgs = req.get("messages") or []
            if not msgs:
                self._send(400, {"error": {"message": "messages required"}})
                return
            text, used, err = complete(msgs, req.get("model", ""),
                                       int(req.get("max_tokens") or 2048))
            if text is None:
                self._send(502, {"error": {"message": err, "type": "upstream_error"}})
                return
            self._send(200, {
                "id": f"or-{int(time.time()*1000)}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": used,
                "choices": [{"index": 0,
                             "message": {"role": "assistant", "content": text},
                             "finish_reason": "stop"}],
            })
        else:
            self._send(404, {"error": {"message": "not found"}})


def main():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.settimeout(1.0)
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            print(f"[OR] ABORT: port {PORT} in use by another process")
            raise SystemExit(1)
    finally:
        probe.close()

    print("[OR] Isolated OpenRouter free server")
    print(f"[OR] credential : {'present' if api_key() else 'MISSING (set OR_API_KEY)'}")
    print(f"[OR] free models: {len(free_models())}")
    print(f"[OR] serving on : http://{HOST}:{PORT}")
    print(f"[OR] OpenAI base: http://{HOST}:{PORT}/v1")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()