"""
Super-Soldier Answer Ladder
===========================
Answers as much as possible without calling an LLM.

Ladder order (each rung must return a REAL answer to count):
  1. cache   — exact/near-exact repeat of a question already answered
  2. python  — deterministic compute (math, dates, units, currency)
  3. local   — files, SQLite, vault memory
  4. web     — live fetch (opt-in only)
  5. mcp     — domain bot/MCP interaction (opt-in only)
  6. llm     — last resort

Hard rule: a rung NEVER fabricates. If it cannot produce a real answer it
returns None and the ladder falls through. An honest miss beats a fake success.

Every answer carries `served_by` so it is always visible what actually answered.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

# ── Config ─────────────────────────────────────────────────────────
CACHE_DB = os.environ.get(
    "SS_CACHE_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "supersoldier_cache.db"),
)
CACHE_TTL = int(os.environ.get("SS_CACHE_TTL", str(60 * 60 * 24 * 14)))  # 14 days
WEB_ENABLED = os.environ.get("SS_WEB", "0") == "1"
MCP_ENABLED = os.environ.get("SS_MCP", "0") == "1"


def _norm_key(text: str) -> str:
    """Normalise a question into a stable cache key."""
    t = text.strip().lower()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^\w\s]", "", t)
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:32]


# ── Rung 1: cache ──────────────────────────────────────────────────
class CacheRung:
    """Disk-backed so answers survive restart and rate-limit walls."""

    name = "cache"

    def __init__(self, db_path: str = CACHE_DB, ttl: int = CACHE_TTL):
        self.ttl = ttl
        self._lock = threading.Lock()
        self.db_path = db_path
        self.hits = 0
        self.misses = 0
        self._init_db()

    def _init_db(self):
        with self._lock:
            con = sqlite3.connect(self.db_path)
            con.execute(
                """CREATE TABLE IF NOT EXISTS answers(
                       key TEXT PRIMARY KEY,
                       question TEXT,
                       answer TEXT,
                       served_by TEXT,
                       persona TEXT,
                       created REAL,
                       hits INTEGER DEFAULT 0)"""
            )
            con.commit()
            con.close()

    def get(self, question: str, persona: str = "") -> Optional[str]:
        key = _norm_key(f"{persona}::{question}")
        with self._lock:
            con = sqlite3.connect(self.db_path)
            row = con.execute(
                "SELECT answer, created, served_by FROM answers WHERE key=?", (key,)
            ).fetchone()
            if not row:
                self.misses += 1
                con.close()
                return None
            answer, created, served_by = row
            if self.ttl and (time.time() - created) > self.ttl:
                con.execute("DELETE FROM answers WHERE key=?", (key,))
                con.commit()
                con.close()
                self.misses += 1
                return None
            con.execute("UPDATE answers SET hits=hits+1 WHERE key=?", (key,))
            con.commit()
            con.close()
        self.hits += 1
        return answer

    def put(self, question: str, answer: str, served_by: str, persona: str = ""):
        key = _norm_key(f"{persona}::{question}")
        with self._lock:
            con = sqlite3.connect(self.db_path)
            con.execute(
                "INSERT OR REPLACE INTO answers(key,question,answer,served_by,persona,created,hits)"
                " VALUES(?,?,?,?,?,?,0)",
                (key, question, answer, served_by, persona, time.time()),
            )
            con.commit()
            con.close()

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            con = sqlite3.connect(self.db_path)
            n = con.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
            con.close()
        return {"entries": n, "hits": self.hits, "misses": self.misses,
                "db": self.db_path}


# ── Rung 2: python ─────────────────────────────────────────────────
MATH_PATTERNS = [
    # "what is 12 * 8", "calculate 2+2", "15 percent of 80"
    (re.compile(r"^(?:what(?:'s| is)|calculate|compute|evaluate|solve)\s+(.+?)\s*[?=]?$",
                re.I), "expr"),
]


class PythonRung:
    """Deterministic compute. Returns a REAL answer or None — never a notice."""

    name = "python"

    def __init__(self):
        self.hits = 0

    def can_handle(self, question: str) -> bool:
        return self.answer(question) is not None

    def answer(self, question: str) -> Optional[str]:
        q = question.strip().rstrip("?").strip()
        low = q.lower()

        # Strip conversational prefixes so bare patterns can match
        # ("what is 15 percent of 80" -> "15 percent of 80")
        low = re.sub(r"^(?:what(?:'s| is| are)?|convert|calculate|compute|"
                     r"how much is|how many is|what time is|what date is|"
                     r"tell me)\s+", "", low).strip()
        q = low

        # --- datetime / now — check the RAW question first, prefix-stripping breaks these
        raw_low = question.strip().rstrip("?").strip().lower()
        dt_m = re.match(r"^(?:what(?:'s| is| are)?\s*)?(?:the\s*)?(?:current\s*)?"
                        r"(time|date|day|year)\b", raw_low)
        if dt_m and re.search(r"\b(now|today|current|it)\b", raw_low + " now"):
            now = datetime.now()
            word = dt_m.group(1)
            if word == "time" and "date" not in raw_low:
                return now.strftime("%H:%M:%S")
            if word == "year":
                return str(now.year)
            if word == "day":
                return now.strftime("%A")
            return now.strftime("%Y-%m-%d")

        # --- unit conversion ---
        m = re.match(r"^(\d+(?:\.\d+)?)\s*(mm|cm|m|km|in|inch|inches|ft|feet|kg|g|lb|lbs|"
                     r"c|f|k|kmh|mph)\s+(?:to|in)\s+(mm|cm|m|km|in|inch|inches|ft|feet|kg|g|"
                     r"lb|lbs|c|f|k|kmh|mph)$", low)
        if m:
            val, frm, to = float(m.group(1)), m.group(2), m.group(3)
            conv = {
                ("mm", "cm"): 10, ("cm", "mm"): 0.1, ("m", "cm"): 100, ("cm", "m"): 0.01,
                ("mm", "m"): 0.001, ("m", "mm"): 1000,
                ("kg", "g"): 1000, ("g", "kg"): 0.001, ("lb", "kg"): 0.453592,
                ("kg", "lb"): 2.20462,
                ("c", "f"): lambda x: x * 9 / 5 + 32, ("f", "c"): lambda x: (x - 32) * 5 / 9,
                ("kmh", "mph"): 0.621371, ("mph", "kmh"): 1.60934,
            }
            f = conv.get((frm, to))
            if callable(f):
                return f"{round(f(val), 4)} {to}"
            if f is not None:
                return f"{round(val * f, 4)} {to}"

        # --- percent of ---
        m = re.match(r"^(\d+(?:\.\d+)?)\s*(?:%|percent)\s*of\s*(\d+(?:\.\d+)?)$", low)
        if m:
            pct, base = float(m.group(1)), float(m.group(2))
            return str(round(base * pct / 100, 6))

        # --- word operators: "28 plus 28" -> "28 + 28" ---
        # These arrive constantly from batch/eval work and must never cost an
        # LLM call. Map spoken operators to symbols before the arithmetic check.
        word_ops = [
            (r"\bmultiplied\s+by\b|\btimes\b|\bx\b|\bmultiplied\b", "*"),
            (r"\bdivided\s+by\b|\bdiv\b", "/"),
            (r"\bplus\b|\badded\s+to\b|\band\b", "+"),
            (r"\bminus\b|\bsubtracted\s+by\b|\bless\b", "-"),
        ]
        for pat, sym in word_ops:
            q = re.sub(pat, f" {sym} ", q, flags=re.IGNORECASE)
        q = re.sub(r"\s+", " ", q).strip()

        # --- arithmetic — `q` has already had conversational prefixes stripped,
        # so match a bare expression containing an operator
        m = re.fullmatch(r"[0-9\.\+\-\*\/\(\)\s%]+", q)
        if m and re.search(r"[\+\-\*\/]", q):
            try:
                # Safe eval: no names, no builtins, arithmetic only
                val = eval(compile(ast_expr(q), "<calc>", "eval"),
                           {"__builtins__": {}}, {})
                if isinstance(val, float) and val.is_integer():
                    val = int(val)
                elif isinstance(val, float):
                    val = round(val, 10)
                self.hits += 1
                return str(val)
            except Exception:
                return None
        return None


def ast_expr(expr: str):
    import ast as _ast
    return _ast.Expression(body=_ast.parse(expr, mode="eval").body)


# ── Rung 3: local data ─────────────────────────────────────────────
class LocalRung:
    """Read from local files the user already has. Read-only, allowlisted."""

    name = "local"

    def __init__(self, vault_paths: Optional[List[str]] = None):
        self.hits = 0
        self.vault_paths = vault_paths or []

    def answer(self, question: str) -> Optional[str]:
        low = question.lower()
        # Explicit "search my memory/vault" intent only — never guess.
        if not re.search(r"\b(my memory|my vault|memory\.md|remember|look up in my)\b", low):
            return None
        terms = [t for t in re.findall(r"[a-z0-9_-]{4,}", low)
                 if t not in ("memory", "vault", "remember", "look", "with", "from", "that", "this")]
        if not terms:
            return None
        for p in self.vault_paths:
            if not os.path.isfile(p):
                continue
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except OSError:
                continue
            hits = [ln.strip() for ln in lines
                    if any(t in ln.lower() for t in terms)]
            if hits:
                self.hits += 1
                return "\n".join(hits[:8])
        return None


# ── Rung 4/5: opt-in rungs ─────────────────────────────────────────
class WebRung:
    """Opt-in live fetch. Off unless SS_WEB=1."""

    name = "web"

    def __init__(self):
        self.enabled = WEB_ENABLED
        self.hits = 0

    def answer(self, question: str) -> Optional[str]:
        if not self.enabled:
            return None
        # Requires an explicit fetch instruction — never auto-browse.
        if not re.search(r"\b(fetch|look up online|search the web|latest|current price|news)\b",
                         question, re.I):
            return None
        try:
            import requests  # local import: only needed when enabled
        except ImportError:
            return None
        url_m = re.search(r"https?://\S+", question)
        if not url_m:
            return None
        try:
            r = requests.get(url_m.group(0), timeout=15,
                             headers={"User-Agent": "SuperSoldier/1.0"})
            r.raise_for_status()
            self.hits += 1
            text = re.sub(r"<[^>]+>", " ", r.text)
            return re.sub(r"\s+", " ", text)[:1500]
        except Exception:
            return None


class MCPRung:
    """Opt-in MCP/bot interaction. Off unless SS_MCP=1 and tools registered."""

    name = "mcp"

    def __init__(self):
        self.enabled = MCP_ENABLED
        self.handlers: List[Callable[[str], Optional[str]]] = []
        self.hits = 0

    def register(self, handler: Callable[[str], Optional[str]]):
        self.handlers.append(handler)

    def answer(self, question: str) -> Optional[str]:
        if not self.enabled:
            return None
        for h in self.handlers:
            try:
                out = h(question)
            except Exception:
                continue          # a broken bot must never break the ladder
            if out:
                self.hits += 1
                return out
        return None


# ── The ladder ─────────────────────────────────────────────────────
class AnswerLadder:
    def __init__(self, cache: Optional[CacheRung] = None,
                 local: Optional[LocalRung] = None):
        self.cache = cache or CacheRung()
        self.python = PythonRung()
        self.local = local or LocalRung()
        self.web = WebRung()
        self.mcp = MCPRung()
        self.llm_calls = 0

    @property
    def rungs(self) -> List[Any]:
        return [self.cache, self.python, self.local, self.web, self.mcp]

    def try_answer(self, question: str, persona: str = "") -> Optional[Dict[str, Any]]:
        """Walk the ladder. Returns {'answer':..,'served_by':..} or None."""
        if not question or not question.strip():
            return None

        cached = self.cache.get(question, persona)
        if cached is not None:
            return {"answer": cached, "served_by": "cache"}

        for rung in self.rungs[1:]:          # cache already checked
            fn = getattr(rung, "answer", None)
            if not callable(fn):
                continue
            try:
                out = fn(question)
            except Exception:
                out = None                  # rung errored -> fall through, never fake
            if out is not None and str(out).strip():
                self.cache.put(question, str(out), rung.name, persona)
                return {"answer": str(out), "served_by": rung.name}

        return None                          # caller must use the LLM

    def record_llm_answer(self, question: str, answer: str, persona: str = ""):
        self.llm_calls += 1
        self.cache.put(question, answer, "llm", persona)

    def stats(self) -> Dict[str, Any]:
        return {
            "cache": self.cache.stats(),
            "python_hits": self.python.hits,
            "local_hits": self.local.hits,
            "web_enabled": self.web.enabled,
            "web_hits": self.web.hits,
            "mcp_enabled": self.mcp.enabled,
            "mcp_handlers": len(self.mcp.handlers),
            "llm_calls": self.llm_calls,
        }


if __name__ == "__main__":
    ladder = AnswerLadder()
    tests = [
        "what is 12 * 8",
        "calculate 2+2",
        "what is 15 percent of 80",
        "convert 25 c to f",
        "10 mm to cm",
        "what time is it",
        "Who wrote Hamlet?",
        "what is 100 / 7",
    ]
    for t in tests:
        r = ladder.try_answer(t)
        if r:
            print(f"  {t:<34} -> [{r['served_by']}] {r['answer'][:60]}")
        else:
            print(f"  {t:<34} -> [LLM required]")
    print("\nstats:", json.dumps(ladder.stats(), indent=2))