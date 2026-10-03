"""
Per-client access control + usage logging for Super-Soldier.

Why this exists: an unauthenticated endpoint on a LAN is one typo away from
anyone spending your API quota. Per-client keys make every request
attributable, revocable, and limitable — which is also what makes usage
monitoring meaningful rather than just an alarm.

Client keys live in .env as SS_CLIENT_KEYS:
    SS_CLIENT_KEYS=bruce:KEY1,luke:KEY2,simone:KEY3
Keys are never logged in full — only a masked prefix.

Env:
    SS_REQUIRE_AUTH=1     enforce keys (default 1 when clients are configured)
    SS_RATE_LIMIT=60       requests per minute per client (0 = unlimited)
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

# If SS_ENV_PATH names a directory, append .env. Accepting both forms avoids a
# silent "no clients configured" failure that looks like auth is disabled.
_env_override = os.environ.get("SS_ENV_PATH", "")
if _env_override:
    ENV_PATH = (_env_override if _env_override.endswith(".env")
                else os.path.join(_env_override, ".env"))
else:
    ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
LOG_DB = os.environ.get(
    "SS_ACCESS_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "supersoldier_access.db"),
)
RATE_LIMIT = int(os.environ.get("SS_RATE_LIMIT", "60"))   # per client per minute


def _read_env(env_path: str = ENV_PATH) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not os.path.isfile(env_path):
        return out
    try:
        with open(env_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def mask(key: str) -> str:
    """Enough to identify a key in a log, useless if the log leaks."""
    if not key:
        return "none"
    return f"{key[:4]}...{key[-2:]}" if len(key) > 10 else "***"


class AuthResult:
    def __init__(self, ok: bool, client: str = "anonymous", reason: str = ""):
        self.ok = ok
        self.client = client
        self.reason = reason

    def __repr__(self):
        return f"AuthResult(ok={self.ok}, client={self.client!r}, reason={self.reason!r})"


class AccessLog:
    """SQLite request log + per-client rate limiting."""

    def __init__(self, db_path: str = LOG_DB):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()
        self._buckets: Dict[str, List[float]] = {}
        self._clients = self._load_clients()

    # ── setup ──────────────────────────────────────────────────────
    def _init_db(self):
        with self._lock:
            con = sqlite3.connect(self.db_path)
            con.execute(
                """CREATE TABLE IF NOT EXISTS requests(
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       ts REAL, client TEXT, client_key_masked TEXT,
                       path TEXT, model TEXT, served_by TEXT,
                       llm_called INTEGER, latency_ms REAL,
                       status INTEGER)"""
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_ts ON requests(ts)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_client ON requests(client)")
            con.commit()
            con.close()

    def _load_clients(self) -> Dict[str, str]:
        """name -> hashed key. We store a hash, not the raw key."""
        raw = _read_env().get("SS_CLIENT_KEYS", "")
        clients: Dict[str, str] = {}
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            name, key = pair.split(":", 1)
            name, key = name.strip(), key.strip()
            if name and key:
                clients[name] = hashlib.sha256(key.encode()).hexdigest()
        return clients

    def reload(self):
        self._clients = self._load_clients()
        return len(self._clients)

    @property
    def auth_required(self) -> bool:
        if os.environ.get("SS_REQUIRE_AUTH") == "0":
            return False
        return bool(self._clients)

    # ── auth ───────────────────────────────────────────────────────
    def authenticate(self, provided: str) -> AuthResult:
        if not self.auth_required:
            return AuthResult(True, "open")
        if not provided:
            return AuthResult(False, "anonymous", "missing api key")
        h = hashlib.sha256(provided.encode()).hexdigest()
        for name, want in self._clients.items():
            if h == want:                     # constant-time-ish compare below
                return AuthResult(True, name)
        return AuthResult(False, "unknown", "invalid api key")

    # ── rate limit ─────────────────────────────────────────────────
    def check_rate(self, client: str, now: Optional[float] = None) -> Tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        if not RATE_LIMIT:
            return True, 0.0
        now = now or time.time()
        window = now - 60.0
        with self._lock:
            hits = [t for t in self._buckets.get(client, []) if t > window]
            if len(hits) >= RATE_LIMIT:
                self._buckets[client] = hits
                return False, round(hits[0] + 60.0 - now, 2)
            hits.append(now)
            self._buckets[client] = hits
        return True, 0.0

    # ── logging ────────────────────────────────────────────────────
    def record(self, client: str, key_masked: str, path: str, model: str,
               served_by: str, llm_called: bool, latency_ms: float, status: int):
        try:
            with self._lock:
                con = sqlite3.connect(self.db_path)
                con.execute(
                    "INSERT INTO requests(ts,client,client_key_masked,path,model,"
                    "served_by,llm_called,latency_ms,status) VALUES(?,?,?,?,?,?,?,?,?)",
                    (time.time(), client, key_masked, path, model,
                     served_by, 1 if llm_called else 0, round(latency_ms, 1), status))
                con.commit()
                con.close()
        except sqlite3.Error:
            pass          # logging must never break a request

    # ── reporting ──────────────────────────────────────────────────
    def stats(self, since: float = 0.0) -> Dict[str, Any]:
        with self._lock:
            con = sqlite3.connect(self.db_path)
            con.row_factory = sqlite3.Row
            total = con.execute(
                "SELECT COUNT(*) c, SUM(llm_called) llm FROM requests WHERE ts>?",
                (since,)).fetchone()
            by_client = con.execute(
                "SELECT client, COUNT(*) requests, SUM(llm_called) llm,"
                " ROUND(AVG(latency_ms),1) avg_ms FROM requests WHERE ts>?"
                " GROUP BY client ORDER BY requests DESC", (since,)).fetchall()
            by_served = con.execute(
                "SELECT served_by, COUNT(*) c FROM requests WHERE ts>?"
                " GROUP BY served_by ORDER BY c DESC", (since,)).fetchall()
            hourly = con.execute(
                "SELECT strftime('%H:00', ts, 'unixepoch', 'localtime') h, COUNT(*) c"
                " FROM requests WHERE ts>? GROUP BY h ORDER BY h", (since,)).fetchall()
            recent = con.execute(
                "SELECT ts, client, path, model, served_by, llm_called, latency_ms, status"
                " FROM requests ORDER BY id DESC LIMIT 20", ()).fetchall()
            con.close()

        req = total["c"] or 0
        llm = total["llm"] or 0
        return {
            "auth_required": self.auth_required,
            "clients_configured": sorted(self._clients.keys()),
            "rate_limit_per_min": RATE_LIMIT,
            "window_seconds": round(time.time() - since, 1) if since else None,
            "totals": {
                "requests": req,
                "llm_calls": llm,
                "free_calls": req - llm,
                "free_pct": round(100 * (req - llm) / req, 1) if req else 0.0,
            },
            "by_client": [dict(r) for r in by_client],
            "by_served_by": [dict(r) for r in by_served],
            "hourly": [dict(r) for r in hourly],
            "recent": [dict(r) for r in recent],
        }

    def purge(self, older_than_days: int = 30) -> int:
        cutoff = time.time() - older_than_days * 86400
        with self._lock:
            con = sqlite3.connect(self.db_path)
            n = con.execute("DELETE FROM requests WHERE ts<?", (cutoff,)).rowcount
            con.commit()
            con.close()
        return n


if __name__ == "__main__":
    import json
    al = AccessLog()
    print(json.dumps({k: v for k, v in al.stats().items() if k != "recent"},
                     indent=2)[:1200])