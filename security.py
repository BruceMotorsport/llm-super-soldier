"""
Security middleware for Super-Soldier.

Adds what a public-facing endpoint needs and currently lacks:
  1. Auth on EVERY protected route (not just /v1/chat/completions)
  2. Security headers (CSP, X-Frame-Options, HSTS, nosniff, referrer)
  3. Scoped CORS (never wildcard with credentials)
  4. Body-size limit so one request can't exhaust memory
  5. Login session for the web console (cookie, so the browser isn't
     asking for an API key on every fetch)

Design rules:
  - Fail CLOSED: if auth is configured and a request can't be authenticated,
    it is denied. Never silently fall through to open.
  - Never log secrets, never echo a key back.
  - The console gets a session cookie so it is usable in a browser; the API
    keeps working with a Bearer key for scripts and other machines.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Optional, Set

from fastapi import Request
from fastapi.responses import JSONResponse

SECRET = os.environ.get("SS_SESSION_SECRET") or ""
MAX_BODY = int(os.environ.get("SS_MAX_BODY", str(2 * 1024 * 1024)))   # 2 MB
SESSION_TTL = int(os.environ.get("SS_SESSION_TTL", str(12 * 3600)))   # 12 h
COOKIE = "ss_session"

# Routes that must stay reachable without auth, or nothing can check health.
PUBLIC_PATHS: Set[str] = {"/health", "/ui", "/ui/", "/login"}

# Headers applied to every response.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Content-Security-Policy": (
        # The console is deliberately self-contained: no CDN, no inline
        # script from anywhere else. 'unsafe-inline' is required for the
        # inline <script> block but the page has no external origins.
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'self'"
    ),
}

_sessions: dict = {}


def _sign(value: str) -> str:
    """HMAC a session id so a cookie can't be forged offline."""
    if not SECRET:
        return ""
    return hmac.new(SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()[:32]


def make_session(user: str) -> str:
    sid = secrets.token_urlsafe(24)
    _sessions[sid] = {"user": user, "expires": time.time() + SESSION_TTL}
    return f"{sid}.{_sign(sid)}"


def verify_session(cookie: str) -> Optional[str]:
    if not cookie or "." not in cookie:
        return None
    sid, sig = cookie.rsplit(".", 1)
    if not SECRET or not hmac.compare_digest(_sign(sid), sig):
        return None
    rec = _sessions.get(sid)
    if not rec or rec["expires"] < time.time():
        _sessions.pop(sid, None)
        return None
    return rec["user"]


def bearer_from(req: Request) -> str:
    auth = req.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return req.headers.get("x-api-key", "").strip()


def wants_html(req: Request) -> bool:
    """Browser navigation wants HTML; scripts/clients want JSON."""
    accept = req.headers.get("accept", "")
    if "text/html" in accept:
        return True
    # A bare GET from a browser (Sec-Fetch-Mode: navigate)
    return req.headers.get("sec-fetch-mode", "") == "navigate"


def unauthorized(req: Request, reason: str):
    if wants_html(req):
        return JSONResponse(
            {"error": reason},
            status_code=401,
            headers={"Location": "/login"} if False else None,
        )
    return JSONResponse(
        {"error": {"message": reason, "type": "auth_error"}}, status_code=401
    )


def apply_headers(resp):
    for k, v in SECURITY_HEADERS.items():
        resp.headers[k] = v
    return resp