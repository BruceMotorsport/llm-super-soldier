#!/usr/bin/env python3
"""
Isolated OpenCode Free Server  (standalone process, port 8086)
=============================================================
Author: Buddy | Directive: Bruce — "open code free models... service the
opencode software only. what we did is we made a server that pretends to be
open code... the output end should output openai compatibility."

This is that server. It impersonates the OpenCode endpoint so nothing has to
know the OpenCode CLI exists:

    OpenAI-compatible OUT  →  OpenCode CLI IN

    POST /v1/chat/completions {"model":"opencode/mimo-v2.6-flash-free", ...}
        → shells out to `opencode run -m <model> <prompt>`
        → returns a normal OpenAI chat.completion

ISOLATION (Bruce directive): own process, own port, NO shared code with
Super-Soldier / Groq / OpenRouter. Super-Soldier reaches this over HTTP as a
peer. If the OpenCode CLI is missing or the account is unauthenticated, this
server reports it honestly and exits nothing into anyone else's process.

OpenCode free models need an OpenCode account credential (not an API key).
Measured 2026-10-04 on this machine: auth.json held only an OpenRouter key,
so every opencode/* model fails with 503 after ~30s. That is a CREDENTIAL
state, not a code fault — see /health for the current truth.

Run:  python oc_server.py         (127.0.0.1:8086)
Env:  OC_HOST, OC_PORT, OC_TIMEOUT, OC_PATH
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

HOST = os.getenv("OC_HOST", "127.0.0.1")
PORT = int(os.getenv("OC_PORT", "8086"))
TIMEOUT = int(os.getenv("OC_TIMEOUT", "120"))
OC_PATH = os.getenv("OC_PATH", "")

AUTH_PATH = os.path.join(os.path.expanduser("~"), ".local", "share",
                         "opencode", "auth.json")

# Model ids exactly as the OpenCode ecosystem names them.
MODELS = [
    "opencode/mimo-v2.6-flash-free",
    "opencode/nemotron-3-ultra-free",
    "opencode/nemotron-3.5-lightning-free",
    "opencode/ling-3.0-flash-fin-free",
    "opencode/muse-spark-1.3-contributor-free",
    "opencode/jev-1.13-free",
]

_lock = threading.Lock()
_stats = {"requests": 0, "served": 0, "failed": 0, "by_model": {}}


def find_opencode() -> Optional[str]:
    if OC_PATH and os.path.exists(OC_PATH):
        return OC_PATH
    for c in (r"C:\Users\Bruce\nodejs\opencode.cmd",
              r"C:\Users\Bruce\AppData\Roaming\npm\opencode.cmd",
              r"C:\Program Files\nodejs\opencode.cmd",
              shutil.which("opencode"), shutil.which("opencode.cmd")):
        if c and os.path.exists(c):
            return c
    return None


def has_opencode_account() -> bool:
    """True only if a real OpenCode credential exists (not just OpenRouter)."""
    try:
        with open(AUTH_PATH, encoding="utf-8") as f:
            keys = json.load(f)
        return any(k.lower() not in ("openrouter",) for k in keys)
    except (OSError, ValueError):
        return False


def _run(prompt: str, model: str, timeout: int = TIMEOUT) -> Tuple[int, str, str]:
    exe = find_opencode()
    if not exe:
        return 127, "", "opencode CLI not found"
    user_msg = (prompt or "").replace("\x00", "").strip() or "(empty)"
    try:
        p = subprocess.run([exe, "run", "-m", model, user_msg],
                           capture_output=True, text=True, timeout=timeout,
                           cwd=os.environ.get("USERPROFILE", os.getcwd()))
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except Exception as e:
        return 1, "", f"{type(e).__name__}: {str(e)[:120]}"


async def complete(messages: List[Dict], model: str = "",
                   timeout: int = TIMEOUT) -> Tuple[Optional[str], str, Optional[str]]:
    """Returns (content, model_used, error)."""
    mdl = model if model in MODELS else MODELS[0]
    if not mdl.startswith("opencode/"):
        mdl = MODELS[0]
    prompt = "\n".join(m.get("content") or "" for m in messages
                       if m.get("role") in ("user", "system"))
    rc, out, err = await asyncio.to_thread(_run, prompt, mdl, timeout)
    if rc != 0 or not out or '"type":"error"' in out:
        with _lock:
            _stats["failed"] += 1
        reason = err or out or f"rc={rc}"
        # Be explicit about the known credential cause rather than a bare 503.
        if rc == 124:
            reason = f"opencode timed out after {timeout}s"
        elif not has_opencode_account():
            reason = (f"no OpenCode account credential on this machine "
                      f"(rc={rc}); OpenCode free models need one")
        return None, mdl, reason[:300]
    with _lock:
        _stats["served"] += 1
        _stats["by_model"][mdl] = _stats["by_model"].get(mdl, 0) + 1
    return out, mdl, None


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

    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path.split("?")[0]
        cli = find_opencode()
        acct = has_opencode_account()
        if p in ("/health", "/healthz"):
            self._send(200, {
                "status": "ok",
                "server": "opencode-bridge",
                "port": PORT,
                "cli": os.path.basename(cli) if cli else "MISSING",
                "opencode_account_credential": acct,
                "serving": bool(cli and acct),
                "note": ("OpenCode free models need an OpenCode account "
                         "credential, not an API key") if not acct else "",
            })
        elif p in ("/v1/models", "/models"):
            with _lock:
                _stats["requests"] += 1
            self._send(200, {"object": "list", "data": [
                {"id": m, "object": "model", "owned_by": "opencode",
                 "context_window": 32768} for m in MODELS]})
        elif p == "/stats":
            self._send(200, {"status": "ok", "cli_present": bool(cli),
                             "account_credential": acct, **(_stats | {})})
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
            content, used, err = asyncio.run(
                complete(msgs, req.get("model", ""), TIMEOUT))
            if content is None:
                self._send(502, {"error": {"message": err, "type": "upstream_error"}})
                return
            self._send(200, {
                "id": f"oc-{int(time.time()*1000)}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": used,
                "choices": [{"index": 0,
                             "message": {"role": "assistant", "content": content},
                             "finish_reason": "stop"}],
            })
        else:
            self._send(404, {"error": {"message": "not found"}})


def main():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.settimeout(1.0)
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            print(f"[OC] ABORT: port {PORT} in use")
            raise SystemExit(1)
    finally:
        probe.close()

    cli = find_opencode()
    acct = has_opencode_account()
    print("[OC] Isolated OpenCode bridge (pretends to be OpenCode, speaks OpenAI)")
    print(f"[OC] cli          : {cli or 'MISSING'}")
    print(f"[OC] account cred : {'yes' if acct else 'NO — models will 502'}")
    print(f"[OC] models       : {len(MODELS)}")
    print(f"[OC] serving on   : http://{HOST}:{PORT}")
    print(f"[OC] OpenAI base  : http://{HOST}:{PORT}/v1")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()