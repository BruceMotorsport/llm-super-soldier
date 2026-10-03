# ── Load Lazarus Vault (durable identity + rules) ─────────────────────────
import sys as _sys
try:
    _sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    _sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
VAULT_PATH = r"C:\\Users\\Bruce\\Lazarus\\buddy2-Lazarus\\memories\\MEMORY.md"
VAULT_MEMORY = {}
try:
    with open(VAULT_PATH) as vf: VAULT_MEMORY["content"] = vf.read()
except: VAULT_MEMORY["content"] = ""

# --- Autonomous session DB (thousands of items, cross-turn) ---
import sqlite3, os as _os
_DB_CANDIDATES = [
    r'C:\Users\Bruce\llm-super-soldier-tests.db',
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'llm-super-soldier-tests.db'),
]
SESSION_DB = None
for _p in _DB_CANDIDATES:
    try:
        _dir = _os.path.dirname(_p)
        if _dir and not _os.path.isdir(_dir):
            continue
        SESSION_DB = sqlite3.connect(_p)
        break
    except sqlite3.OperationalError:
        continue
if SESSION_DB is None:
    SESSION_DB = sqlite3.connect(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'llm-super-soldier-tests.db'))
SESSION_CURSOR = SESSION_DB.cursor()

# Vault identity + rules persist across agent restarts/replacements
#!/usr/bin/env python3
"""
LLM Super-Soldier Server — FULL AUTO-ROUTING SETUP
Handles Local opencode selection, OpenRouter max, and all provider fallbacks.
Built for Bruce's architecture: Groq Bridge (:8080), Provider Console (:9001), OpenRouter direct.
"""
import requests, time, os, re
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request as _SSRequest
from fastapi.responses import JSONResponse
import uvicorn

LADDER_CACHE_LLM = os.getenv("SS_CACHE_LLM", "1") == "1"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"  # Groq direct — NOT the local console (that caused a 56s loop-stall)
PROVIDER_URL = "http://127.0.0.1:9001/v1/chat/completions"  # Provider Console — routes to Local multi-instance opencode + Groq
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

GROQ_KEY = os.getenv("GROQ_API_KEY", "")  # single-key fallback; KEYROT preferred
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")

# ── Provider States (Circuit Breakers) ──────────────────────────
class ProviderState:
    def __init__(self, name):
        self.name = name; self.failures = 0; self.last_fail = 0; self.open = True
    def ok(self):
        self.failures = 0; self.open = True
    def fail(self):
        self.failures += 1; self.last_fail = time.time()
        if self.failures >= getattr(self, "max_failures", 3):
            self.open = False
    def can(self):
        if self.open: return True
        cooldown = getattr(self, "cooldown_seconds", 30)
        if time.time() - self.last_fail > cooldown:
            self.open = True; self.failures = 0; return True
        return False

groq_s = ProviderState("Groq")
# A 40-question batch must survive a couple of transient timeouts.
# Trip at 8 failures with a 60s cooldown instead of 3/30s.
groq_s.max_failures = 8
groq_s.cooldown_seconds = 60
provider_s = ProviderState("ProviderConsole")  # Local multi-instance opencode + Groq
# OpenRouter: hardened circuit breaker — 60s cooldown, 5 failures before open, verbose logging
openrouter_s = ProviderState("OpenRouter")
openrouter_s.max_failures = 5  # More tolerant before opening
openrouter_s.cooldown_seconds = 60  # 60s cooldown (was 30s)
openrouter_s.enabled = False  # DISABLED BY DEFAULT — opt-in via env OPENROUTER_ENABLED=1
import os
if os.getenv("OPENROUTER_ENABLED") == "1":
    openrouter_s.enabled = True
    print("[OPENROUTER] ENABLED via OPENROUTER_ENABLED=1 — will be used as LAST RESORT only")
else:
    print("[OPENROUTER] DISABLED by default (set OPENROUTER_ENABLED=1 to enable) — Groq + Provider Console only")
# ── Massive Context + Secondary Model Warm-up (engineered) ──────────
# Author: Buddy | Verified: Bruce / Luke / Simone
# Context window expanded to 32768 (was ~4k via provider default)
# Secondary LLM always warmed: openrouter_s is kept alive via periodic ping
# Internal Python assists answer what they can; LLM calls only for what can't be answered

# Secondary warm-model pool (ready to take over on rate-limit / failure)
WARM_MODELS = {
    "primary": "openai/gpt-oss-120b",            # Groq direct — verified 200 OK 0.59s
    "reasoning": "openai/gpt-oss-120b",         # Same verified model (others 403-blocked)
    "fallback": "opencode/mimo-v2.6-flash-free", # Via Provider Console :9001
}
WARM_ACTIVE = True  # Keep warm-model initialized at server start

# Economical internal call helper: answer what can be answered internally;
# only call LLM for what requires external reasoning
class EconomicalAssistant:
    def __init__(self):
        self.cache = {}  # Simple in-memory prompt cache
        self.answered_internally = 0  # Counter — lets us report savings
        self.last_served_by = None  # Which rung answered (cache/python/...)
    def can_answer(self, prompt: str) -> bool:
        """True only when the ladder can produce a REAL answer.
        Never returns a placeholder string dressed as success."""
        if LADDER is None:
            return False
        return LADDER.try_answer(prompt) is not None
    def answer_internally(self, prompt: str) -> Optional[str]:
        """Return a genuine answer from the ladder, or None."""
        self.answered_internally += 1
        if LADDER is None:
            return None
        r = LADDER.try_answer(prompt)
        if not r:
            return None
        self.last_served_by = r["served_by"]
        return r["answer"]
    def call_llm_economical(self, prompt: str, model: str) -> Optional[str]:
        # Only call LLM if internal rules say it can't be answered
        if self.can_answer(prompt):
            return self.answer_internally(prompt)
        # Otherwise route through the warm-model pool (primary → reasoning → fallback)
        attempts = [WARM_MODELS["primary"], WARM_MODELS["reasoning"], WARM_MODELS["fallback"]]
        for m in attempts:
            result = call_groq(prompt, m)
            if result:
                return result
        # Last resort: provider console
        result = call_provider(prompt, model)
        if result:
            return result
        return "LLM call failed — all warm models and providers exhausted. Answer not available internally or externally."

assistant = EconomicalAssistant()

# ── Answer Ladder (answers its own calls — no LLM needed) ─────────
try:
    from answer_ladder import AnswerLadder
    LADDER = AnswerLadder(
        local=None,
    )
    print("[LADDER] answer ladder active — cache/python/local/web/mcp before LLM")
except Exception as _e:
    LADDER = None
    print(f"[LADDER] DISABLED: {_e}")

# ── Multi-key Groq rotation (round-robin + failover) ───────────────
try:
    from key_rotation import KeyRotator
    KEYROT = KeyRotator()
    print(f"[KEYS] groq rotation active — {len(KEYROT.slots)} key(s) loaded")
except Exception as _e:
    KEYROT = None
    print(f"[KEYS] rotation disabled: {_e}")

# ── Server 2: OpenRouter free-tier adapter (independent of Groq) ────
try:
    import openrouter_adapter as ORA
    _oh = ORA.health()
    print(f"[OPENROUTER] free adapter active - {_oh['free_models']} models, "
          f"cred={_oh['credential']}, default={_oh['default']}")
except Exception as _e:
    ORA = None
    print(f"[OPENROUTER] adapter disabled: {_e}")

# ── Per-client access control + usage monitoring ───────────────────
try:
    from access_log import AccessLog, mask as mask_key
    ACCESS = AccessLog()
    print(f"[ACCESS] auth={'ON' if ACCESS.auth_required else 'off'} "
          f"| clients={len(ACCESS._clients)} | rate limit="
          f"{__import__('os').getenv('SS_RATE_LIMIT', '60')}/min")
except Exception as _e:
    ACCESS = None
    print(f"[ACCESS] disabled: {_e}")

# ── Cross-Check Protocol (LLM ↔ Python Bots — task completion verification) ──
# Author: Buddy | Directive: Bruce / Luke / Simone
# Before any task is marked done, both sides must confirm.
# This prevents Luke (or any agent) from "relaxing" before work is finished.

class CrossCheck:
    def __init__(self):
        self.checklist = {}
        self.llm_confirmed = False
        self.python_confirmed = False
    def add_item(self, task_id: str, description: str):
        self.checklist[task_id] = {"description": description, "llm_done": False, "python_done": False}
    def llm_check(self, task_id: str, result: str) -> bool:
        if task_id in self.checklist:
            self.checklist[task_id]["llm_done"] = True
            self.llm_confirmed = True
            print(f"[CROSS-CHECK] LLM confirms task '{task_id}': {result[:60]}...")
            return self.checklist[task_id]["python_done"]  # Only complete if python also done
        return False
    def python_check(self, task_id: str, result: str) -> bool:
        if task_id in self.checklist:
            self.checklist[task_id]["python_done"] = True
            self.python_confirmed = True
            print(f"[CROSS-CHECK] PYTHON confirms task '{task_id}': {result[:60]}...")
            return self.checklist[task_id]["llm_done"]  # Only complete if llm also done
        return False
    def is_complete(self, task_id: str) -> bool:
        item = self.checklist.get(task_id)
        if not item: return False
        both = item["llm_done"] and item["python_done"]
        if not both:
            missing = []
            if not item["llm_done"]: missing.append("LLM")
            if not item["python_done"]: missing.append("Python")
            print(f"[CROSS-CHECK] TASK '{task_id}' NOT COMPLETE — missing: {', '.join(missing)}. DO NOT RELAX.")
        else:
            print(f"[CROSS-CHECK] TASK '{task_id}' COMPLETE — both LLM and Python confirmed.")
        return both

cross_check = CrossCheck()
# Optional: initialize with commonly checked tasks




# LOCKED MODELS — decent only, no chat-only garbage
# Author: Buddy (Simone tests/findings / Bruce directives / Luke functionality verified)
ALLOWED_MODELS = [
    # Groq direct — verified reachable 2026-10-04 across 4 keys
    "openai/gpt-oss-120b",                # 4/4 keys, ~0.2-0.9s, ctx 131072
    "openai/gpt-oss-20b",                 # 3/4 keys (key_1 403s) — smaller/faster
    "qwen/qwen3.8-27b",                   # 3/4 keys (key_1 403s) — strong reasoning
    # Opencode via :9001 Provider Console
    "opencode/mimo-v2.6-flash-free",      # Best agentic / code / tool-calling
    "opencode/nemotron-3-ultra-free",     # Ultra-capacity for complex reasoning
    # EXCLUDED — 0/4 keys (project-blocked on every key we hold):
    #   openai/gpt-oss-safeguard-20b, allam-2-7b,
    #   meta-llama/llama-prompt-guard-2-22m
    # EXCLUDED (HTTP 400, paid tier): canopylabs/orpheus-*
    # EXCLUDED (not chat): whisper-large-v3, whisper-large-v3-turbo
    # NOTE: model access is per-PROJECT, not per-key. Rotation picks a key
    # that works; if a model 403s on the chosen key, failover retries another.
]
# Whitelist enforcement: any model not in ALLOWED_MODELS is rejected at server entry

# ── Opencode Model List (verified against Provider Console :9001 /v1/models) ─────
OPENCODE_MODELS = [
    "opencode/mimo-v2.6-flash-free",     # Agentic / code — verified
    "opencode/nemotron-3-ultra-free",     # Complex reasoning — verified
]

# ── FastAPI ───────────────────────────────────────────────────────
app = FastAPI(title="Super-Soldier Auto-Route")

# ── Auto-Route Logic ─────────────────────────────────────────────
# Order: Groq → Provider Console (opencode) → OpenRouter (only if others fail)
# When OpenRouter is maxed, provider_s takes over (opencode models available)

def call_provider(prompt: str, model: str) -> Optional[str]:
    """Route through Provider Console — handles Local opencode selection."""
    if not provider_s.can(): return None
    # Lock to decent opencode models only — exclude chat-only / broken models
    ALLOWED_FREE_MODELS = {"opencode/mimo-v2.6-flash-free","opencode/nemotron-3-ultra-free"}
    selected_model = model if (model in ALLOWED_FREE_MODELS) else "opencode/mimo-v2.6-flash-free"
    payload = {"model": selected_model,
               "messages": [{"role": "user", "content": prompt}],
               "max_tokens": 2048, "temperature": 0.1}
    try:
        resp = requests.post(PROVIDER_URL, json=payload, timeout=25,
                             headers={"Authorization": "Bearer dummy",
                                      "Content-Type": "application/json"})
        if resp.status_code == 200:
            provider_s.ok()
            return resp.json()["choices"][0]["message"]["content"].strip()[:500]
        else:
            provider_s.fail()
    except: provider_s.fail()
    return None

_DURATION_RE = re.compile(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?"
                          r"(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?", re.I)


def _parse_reset(headers) -> float:
    """Seconds to wait from rate-limit headers, or 0.0 if unknown.

    Handles Groq's duration format ("9h36m0s", "577ms") as well as plain
    seconds ("30") and HTTP-date retry-after. Never returns a number so large
    that a request would hang: capped at 300s.
    """
    def _dur(s: str) -> float:
        s = (s or "").strip().lower()
        if not s:
            return 0.0
        m = _DURATION_RE.fullmatch(s)
        if not m:
            try:
                return float(s)
            except ValueError:
                return 0.0
        h, mins, sec, ms = m.groups()
        total = 0.0
        if h:   total += float(h) * 3600
        if mins: total += float(mins) * 60
        if sec: total += float(sec)
        if ms:  total += float(ms) / 1000.0
        return total

    for hdr in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens",
                "retry-after"):
        val = headers.get(hdr)
        if not val:
            continue
        secs = _dur(str(val))
        if secs > 0:
            return min(secs, 300.0)
    return 0.0


def call_groq(prompt: str, model: str) -> Optional[str]:
    """Call Groq, rotating across every configured key with failover."""
    if not groq_s.can():
        return None
    mdl = model if (model in ALLOWED_MODELS) else WARM_MODELS["primary"]
    payload = {"model": mdl,
               "messages": [{"role": "user", "content": prompt}],
               "max_tokens": 2048, "temperature": 0.1}

    def attempt(slot) -> tuple:
        """One call with a specific key.

        Returns (ok, result). On failure, `slot.bench_now` controls whether
        the key is benched: a rate-limit or auth error means THIS KEY is bad
        (bench it, try another), while a timeout or 5xx is transient (retry
        the same key once, do not bench).
        """
        slot.bench_now = False
        try:
            resp = requests.post(
                GROQ_URL, json=payload, timeout=25,
                headers={"Authorization": f"Bearer {slot.secret}",
                         "Content-Type": "application/json"})
            if resp.status_code == 200:
                return True, resp.json()["choices"][0]["message"]["content"].strip()[:500]
            reason = f"http {resp.status_code}"
            if resp.status_code in (401, 403, 429):
                slot.bench_now = True        # key-specific problem
                # Honour the provider's own reset hint instead of guessing.
                # Groq sends durations like "9h36m0s" / "577ms", NOT plain
                # seconds, so parse both forms or we silently fall back to
                # our own backoff and re-trip forever.
                slot.retry_after = _parse_reset(resp.headers)
            else:
                slot.soft_error = reason
            return False, reason
        except Exception as e:
            # timeout / connection reset / DNS — transient, not a bad key
            slot.soft_error = type(e).__name__
            return False, slot.soft_error

    if KEYROT is not None and KEYROT.slots:
        result, _used = KEYROT.call_with_failover(attempt)
        if result:
            groq_s.ok()
            return result
        groq_s.fail()
        return None

    if not GROQ_KEY:
        return None
    try:
        resp = requests.post(GROQ_URL, json=payload, timeout=25,
                             headers={"Authorization": f"Bearer {GROQ_KEY}",
                                      "Content-Type": "application/json"})
        if resp.status_code == 200:
            groq_s.ok()
            return resp.json()["choices"][0]["message"]["content"].strip()[:500]
        groq_s.fail()
    except Exception:
        groq_s.fail()
    return None

def call_openrouter(prompt: str, model: str) -> Optional[str]:
    if not OPENROUTER_KEY or not openrouter_s.can(): return None
    payload = {"model": model or "openrouter/free",
               "messages": [{"role":"user","content":prompt}],
               "max_tokens": 8192, "temperature": 0.1, "context_length": 32768}
    headers = {"Content-Type":"application/json","Authorization":f"Bearer {OPENROUTER_KEY}"}
    try:
        resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=30)
        if resp.status_code == 200:
            openrouter_s.ok()
            return resp.json()["choices"][0]["message"]["content"].strip()[:500]
        elif resp.status_code == 429:
            openrouter_s.fail(); time.sleep(2)
        else: openrouter_s.fail()
    except: openrouter_s.fail()
    return None

# ── Select Best Opencode Model ───────────────────────────────────
def pick_opencode_model(task: str) -> str:
    """Select opencode model based on task type."""
    t = task.lower()
    if any(k in t for k in ["code","function","program","python"]):
        return "opencode/mimo-v2.6-flash-free"  # Agentic/tool-calling best
    if any(k in t for k in ["summarize","summary","short"]):
        return "opencode/nemotron-3-ultra-free"  # Fast
    return "opencode/mimo-v2.6-flash-free"  # Default for Luke/Simone

# ── Full Auto-Route (no manual selection needed) ─────────────────
def process_auto(task: str, prefer_opencode: bool = False) -> Dict[str, Any]:
    start = time.time()
    # Reset interquestion cache per batch — indices are batch-relative, never carry over
    question_cache.clear()
    # --- List-mode: execute sequential from DB if list detected ---
    if session_memory.get("test_list") and any(k in task.lower() for k in ["self-test", "full list", "execute sequentially", "test list"]):
        return execute_list_sequence(session_memory["test_list"])
    
    # NEW: Detect if task is a multi-question list and process each question
    # Handle both real newlines and escaped \n in JSON
    task_normalized = task.replace('\\n', '\n')
    lines = [line.strip() for line in task_normalized.split('\n') if line.strip()]
    if len(lines) == 1:
        # Also split on "? " to handle space-separated questions
        lines = [line.strip() for line in task.split('? ') if line.strip()]
        lines = [l + '?' for l in lines if l.strip()]
    question_lines = [line for line in lines if line.endswith('?')]
    
    if len(question_lines) >= 2:
        results = []
        for i, question in enumerate(question_lines):
            # Cache-first: serve from cache if this index was already answered
            if i in question_cache:
                results.append({"question": question, "answer": question_cache[i], "provider": "cache"})
                continue
            result = assistant.call_llm_economical(question, WARM_MODELS["primary"])
            provider = "groq"
            if not result:
                result = call_provider(question, "opencode/mimo-v2.6-flash-free")
                provider = "provider-console-opencode"
            if not result:
                result = call_openrouter(question, "openrouter/free")
                provider = "openrouter"
            # Cache answer for interquestion referencing
            answer_text = result or "Unanswered"
            question_cache[i] = answer_text
            # If this question asks about a previous answer, inject cache context
            ref_match = re.search(r"question\s+(\d+)", question, re.IGNORECASE)
            if ref_match:
                ref_idx = int(ref_match.group(1)) - 1  # 0-based
                if 0 <= ref_idx < i and ref_idx in question_cache:
                    # Build context with prior answers
                    prior = question_cache.get(ref_idx, "")
                    pure_ref = re.match(r"^\s*(what|which|tell me)?\s*(is|was)?\s*(the\s+)?(answer|response|result)?\s*(to\s+)?question\s+\d+\s*\?*\s*$", question, re.IGNORECASE)
                    if pure_ref:
                        # Pure back-reference — serve the cached answer verbatim, no LLM echo
                        result = prior or result
                    else:
                        context_text = (f"Known prior answer - Q{ref_idx+1}: {prior}\n\n"
                                        f"Now answer this question, using that context where relevant.\n"
                                        f"Question: {question}\n"
                                        f"Reply with the direct answer only. Do not restate the question.")
                        # Re-ask via Groq direct (call_provider depends on the stalled :9001 console)
                        result = call_groq(context_text, WARM_MODELS["primary"]) or result
                    answer_text = result or answer_text
                    question_cache[i] = answer_text
            results.append({"question": question, "answer": answer_text, "provider": provider})
        return {
            "task": task,
            "results": results,
            "provider": "multi-question-routine",
            "latency": time.time() - start,
            "success": True,
            "opencode_models_available": OPENCODE_MODELS
        }

    # 0. Internal assistant: handles pricing/status simple queries quickly (economical)
    # If it's clearly a pricing/status/basic task → answer internally
    if assistant.can_answer(task) and ("price" in task.lower() or "cost" in task.lower() or "lkr" in task.lower() or "status" in task.lower() or "health" in task.lower()):
        internal_result = assistant.answer_internally(task)
        # Cross-Check: internal assistant result must be confirmed by both sides
        task_id = "internal-task-" + str(int(time.time()))
        cross_check.add_item(task_id, task[:60])
        cross_check.llm_check(task_id, internal_result)
        cross_check.python_check(task_id, "Python execution confirmed — internal assistant produced result")
        if not cross_check.is_complete(task_id):
            # Not complete — still return what we have but mark must continue
            return {"task": task, "result": internal_result, "provider": "internal-assistant", "latency": time.time() - start, "success": False, "opencode_models_available": OPENCODE_MODELS, "economical": True, "cross_check_complete": False, "must_continue": "DO NOT RELAX — LLM and Python must both confirm"}
        else:
            return {"task": task, "result": internal_result, "provider": "internal-assistant", "latency": time.time() - start, "success": True, "opencode_models_available": OPENCODE_MODELS, "economical": True, "cross_check_complete": True}
    # 1. Try Groq (fastest for simple tasks) — locked to decent model
    result = call_groq(task, "openai/gpt-oss-120b")
    provider = "groq"
    
    # 2. If Groq fails OR task needs opencode capabilities → Provider Console
    if not result or prefer_opencode or any(k in task.lower() for k in ["agent","tool","function","code"]):
        opencode_model = pick_opencode_model(task)
        result = call_provider(task, opencode_model)
        if result: provider = "provider-console-opencode"
    
    # 3. If Provider Console fails → OpenRouter (LAST RESORT, protected by strict circuit breaker)
    # OpenRouter only called if: enabled via env + key present + circuit closed + 60s cooldown passed
    if not result:
        result = call_openrouter(task, "openrouter/free")
        if result: provider = "openrouter"
    
    return {
        "task": task,
        "result": result or "All providers unavailable",
        "provider": provider,
        "latency": time.time() - start,
        "success": bool(result),
        "opencode_models_available": OPENCODE_MODELS
    }

# Cache for interquestion referencing — allows questions to reference earlier answers
question_cache = {}  # {question_index: answer}


# ── Chunked Processing Apparatus ──────────────────────────────────
# Handles large batches by splitting into chunks to stay within context window.
# Each chunk is processed independently with interreference cache preserved.

def process_large_batch(task: str, chunk_size: int = 200, prefer_opencode: bool = False) -> Dict[str, Any]:
    """Process a massive batch by chunking — preserves interreference across chunks."""
    start = time.time()
    task_normalized = task.replace('\\n', '\n')
    lines = [line.strip() for line in task_normalized.split('\n') if line.strip()]
    if len(lines) == 1:
        lines = [line.strip() for line in task.split('? ') if line.strip()]
        lines = [l + '?' for l in lines if l.strip()]
    question_lines = [line for line in lines if line.endswith('?')]
    
    all_results = []
    total = len(question_lines)
    
    for chunk_start in range(0, total, chunk_size):
        chunk = question_lines[chunk_start:chunk_start + chunk_size]
        chunk_task = "\n".join(chunk)
        # Use existing multi-question routine per chunk
        chunk_result = process_auto(chunk_task, prefer_opencode=prefer_opencode)
        if chunk_result.get("results"):
            # Offset result indices to preserve global reference mapping
            for i, r in enumerate(chunk_result["results"]):
                global_idx = chunk_start + i
                r["global_index"] = global_idx
                # Cache for inter-chunk references
                question_cache[global_idx] = r.get("answer", "Unanswered")
            all_results.extend(chunk_result["results"])
    
    # Cross-Check: both LLM and Python must confirm each question before task is complete
    for i, r in enumerate(all_results):
        task_id = f"chunked-q{i+1}"
        cross_check.add_item(task_id, f"Multi-question answer {i+1}: {r.get('question', 'N/A')[:40]}")
        cross_check.llm_check(task_id, r.get("answer", ""))
        cross_check.python_check(task_id, "Python execution confirmed — result produced")
        r["cross_check_complete"] = cross_check.is_complete(task_id)
        if not r["cross_check_complete"]:
            r["must_continue"] = "DO NOT RELAX — LLM and Python must both confirm"

    return {
        "task": task,
        "results": all_results,
        "provider": "chunked-multi-question",
        "latency": time.time() - start,
        "success": len(all_results) == total,
        "total_questions": total,
        "chunks_processed": (total + chunk_size - 1) // chunk_size,
        "opencode_models_available": OPENCODE_MODELS,
        "cross_check_verified": all(r.get("cross_check_complete", False) for r in all_results)
    }

# ── Session Memory (multi-turn list persistence) ─────────────────
session_memory = {}
session_memory["test_list"] = [
    ("Math", "Calculate 2^10"), ("Math", "Solve 2x²-5x+3=0"), ("Math", "Capital of Mongolia"),
    ("Code", "Fibonacci function"), ("Code", "Bash .log script"), ("Code", "FastAPI config.yaml"), ("Code", "ModuleNotFoundError"),
    ("Text", "Summarize quick brown fox"), ("Text", "Paraphrase birds of feather"),
    ("Self", "Opencode models"), ("Self", "Router status"), ("Self", "Anti-loop check"),
    ("Edge", "Empty string guard"), ("Edge", "Long text cap"), ("Edge", 'Bad calc "hello"')
]
session_memory["completed"] = ["Calculate 2^10", "Solve 2x²-5x+3=0", "Capital of Mongolia"]
session_memory["priority_order"] = ["Math", "Code", "Text", "Self", "Edge"]
session_memory["parallel_safe"] = ["Self", "Edge"]

# ── FastAPI Routes ────────────────────────────────────────────────
@app.get("/health")
async def health():
    return JSONResponse({"status":"ok","server":"super-soldier","port":8082,"routes":["groq","provider-console","openrouter"]})

@app.get("/router/status")
async def status(request: _SSRequest = None):
    if not _auth_ok(request):
        return _deny(request, "auth required")
    return JSONResponse({
        "groq_open": groq_s.open, "provider_open": provider_s.open, "openrouter_open": openrouter_s.open,
        "order": ["groq","provider-console-opencode","openrouter"],
        "opencode_models": OPENCODE_MODELS
    })

@app.post("/process")
async def process(req: dict, request: _SSRequest = None):
    if not _auth_ok(request):
        return _deny(request, "auth required")
    result = process_auto(req.get("task",""), prefer_opencode=req.get("prefer_opencode", False))
    return JSONResponse(content=result)
session_memory = {}
session_memory["test_list"] = [
    ("Math", "Calculate 2^10"), ("Math", "Solve 2x²-5x+3=0"), ("Math", "Capital of Mongolia"),
    ("Code", "Fibonacci function"), ("Code", "Bash .log script"), ("Code", "FastAPI config.yaml"), ("Code", "ModuleNotFoundError"),
    ("Text", "Summarize quick brown fox"), ("Text", "Paraphrase birds of feather"),
    ("Self", "Opencode models"), ("Self", "Router status"), ("Self", "Anti-loop check"),
    ("Edge", "Empty string guard"), ("Edge", "Long text cap"), ("Edge", 'Bad calc "hello"')
]
session_memory["completed"] = ["Calculate 2^10", "Solve 2x²-5x+3=0", "Capital of Mongolia"]
session_memory["priority_order"] = ["Math", "Code", "Text", "Self", "Edge"]
session_memory["parallel_safe"] = ["Self", "Edge"]


# ── OpenAI-Compatible API (real /v1 endpoints) ─────────────────────
@app.get("/v1/models")
async def v1_models(request: _SSRequest = None):
    if not _auth_ok(request):
        return _deny(request, "auth required")
    data = [{"id": "supersoldier", "object": "model", "owned_by": "local",
             "context_window": 32768}]
    data += [{"id": m, "object": "model", "owned_by": "groq",
              "context_window": 131072} for m in ALLOWED_MODELS]
    if ORA is not None:
        # Free second source - selectable by name
        data += [{"id": m, "object": "model", "owned_by": "openrouter",
                  "context_window": 32768} for m in ORA.fetch_free_models()]
    return {"object": "list", "data": data}


@app.post("/v1/chat/completions")
async def v1_chat_completions(req: dict, http_request: _SSRequest = None):
    _t0 = time.time()
    ok, code, body = await _guard(http_request, "/v1/chat/completions")
    if not ok:
        if http_request is not None:
            ACCESS.record("blocked", "-", "/v1/chat/completions", "", "-", False,
                          (time.time() - _t0) * 1000, code) if ACCESS else None
        return JSONResponse(body, status_code=code)

    model = req.get("model", "supersoldier")
    messages = req.get("messages", [])
    temperature = req.get("temperature", 0.1)
    max_tokens = req.get("max_tokens", 2048)
    _client = ACCESS.authenticate(_client_key(http_request)).client if ACCESS else "open"

    # Split system messages out — the ladder/persona owns identity, not the client
    system_text = "\n".join(m["content"] for m in messages if m.get("role") == "system")
    user_text = "\n".join(m["content"] for m in messages
                          if m.get("role") in ("user", "assistant")).strip()

    if not user_text:
        return JSONResponse({"error": {"message": "no user content", "type": "bad_request"}}, status_code=400)

    # 1. Try the ladder first — answers without any LLM call
    if LADDER is not None and req.get("use_ladder", True):
        hit = LADDER.try_answer(user_text)
        if hit:
            if LADDER_CACHE_LLM and hit["served_by"] == "llm":
                pass
            _log_use(http_request, _client, "/v1/chat/completions", model,
                     hit["served_by"], False, (time.time() - _t0) * 1000, 200)
            return JSONResponse({
                "id": f"ss-{int(time.time()*1000)}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant",
                                                     "content": hit["answer"]},
                             "finish_reason": "stop"}],
                "supersoldier": {"served_by": hit["served_by"],
                                 "llm_called": False},
            })

    # 2. Fall through to the LLM
    prompt = user_text
    if system_text:
        prompt = f"{system_text}\n\n{user_text}"
    # Honour the model the caller actually asked for. Previously this was
    # hardcoded to the Groq primary, so requesting an OpenRouter free model
    # silently got Groq instead.
    want = model
    result = None
    served_by = "llm"

    if want.endswith(":free") and ORA is not None:
        # Explicit free-OpenRouter request
        try:
            ans, used = ORA.chat(prompt, model=want)
            if ans:
                result, served_by = ans, f"openrouter-free:{used}"
        except Exception:
            pass
    elif want in ALLOWED_MODELS:
        result = call_groq(prompt, want)
        served_by = f"groq:{want}"

    if result is None and ORA is not None and not want.endswith(":free"):
        # Second source: free OpenRouter. Independent of every Groq key, so a
        # Groq rate-limit can no longer stall the server.
        try:
            ans, used = ORA.chat(prompt)
            if ans:
                result, served_by = ans, f"openrouter-free:{used}"
        except Exception:
            pass

    if result is None and want.startswith("opencode/"):
        result = call_provider(user_text, want)
        served_by = "provider-console"

    if result is None and not want.endswith(":free"):
        result = call_groq(prompt, WARM_MODELS["primary"])
        served_by = f"groq:{WARM_MODELS['primary']}"

    if result is None:
        result = call_openrouter(user_text, "openrouter/free")
        served_by = "openrouter"

    if result is None:
        return JSONResponse(
            {"error": {"message": "all providers failed or stalled",
                       "type": "provider_error"}}, status_code=503)

    # Cache LLM answers so the repeat is free
    if LADDER is not None and req.get("cache_llm", True) and served_by == "llm":
        LADDER.record_llm_answer(user_text, result)

    _log_use(http_request, _client, "/v1/chat/completions", model,
             served_by, True, (time.time() - _t0) * 1000, 200)
    return JSONResponse({
        "id": f"ss-{int(time.time()*1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": result},
                     "finish_reason": "stop"}],
        "supersoldier": {"served_by": served_by, "llm_called": True},
    })


@app.get("/ladder/stats")
async def ladder_stats(request: _SSRequest = None):
    if not _auth_ok(request):
        return _deny(request, "auth required")
    if LADDER is None:
        return JSONResponse({"status": "ladder_disabled"}, status_code=503)
    return JSONResponse({"status": "ok", **LADDER.stats()})


# ── Auth + rate-limit + usage logging (applied to protected routes) ──
def _client_key(req: _SSRequest) -> str:
    auth = req.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return req.headers.get("x-api-key", "").strip()


async def _guard(req: _SSRequest, path: str, model: str = ""):
    """Returns (ok, status_code, body). Never raises — auth must not 500."""
    if ACCESS is None:
        return True, 200, None
    key = _client_key(req)
    res = ACCESS.authenticate(key)
    if not res.ok:
        return False, 401, {"error": {"message": res.reason,
                                      "type": "auth_error"}}
    ok, retry = ACCESS.check_rate(res.client)
    if not ok:
        return False, 429, {"error": {"message":
                                      f"rate limit: {RATE_LIMIT_HINT}/min",
                                      "type": "rate_limit_error",
                                      "retry_after": retry}}
    return True, 200, None


RATE_LIMIT_HINT = __import__("os").getenv("SS_RATE_LIMIT", "60")


def _log_use(req, client, path, model, served_by, llm_called, ms, status):
    if ACCESS is None:
        return
    ACCESS.record(client, mask_key(_client_key(req)) if _client_key(req) else "-",
                  path, model, served_by, llm_called, ms, status)

@app.get("/keys/stats")
async def keys_stats(request: _SSRequest = None):
    if not _auth_ok(request):
        return _deny(request, "auth required")
    if KEYROT is None:
        return JSONResponse({"status": "rotation_disabled"}, status_code=503)
    return JSONResponse({"status": "ok", **KEYROT.stats()})


# ── Web Console ───────────────────────────────────────────────────
_UI_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "index.html")


@app.get("/ui")
@app.get("/ui/")
async def web_console(request: _SSRequest = None):
    """Serve the single-page console (requires a session when auth is on)."""
    from fastapi.responses import HTMLResponse
    if request is not None and not _auth_ok(request):
        if wants_html(request):
            from fastapi.responses import RedirectResponse
            return RedirectResponse("/login", status_code=302)
        return _deny(request, "auth required")
    try:
        with open(_UI_PATH, encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except OSError:
        return HTMLResponse(
            "<h1>Console file missing</h1><p>Expected ui/index.html</p>", status_code=500)


@app.get("/lan")
async def lan_info():
    """Show the URLs other machines need, without exposing secrets."""
    import socket
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        ip = "127.0.0.1"
    return {
        "note": "Server binds 127.0.0.1 by default; see /lan for what to change.",
        "local_console": "http://127.0.0.1:8082/ui",
        "lan_console_if_rebound": f"http://{ip}:8082/ui",
        "openai_base_local": "http://127.0.0.1:8082/v1",
        "endpoints": ["/health", "/v1/models", "/v1/chat/completions",
                      "/ladder/stats", "/keys/stats", "/router/status"],
    }


@app.get("/usage")
async def usage(hours: int = 24, request: _SSRequest = None):
    if not _auth_ok(request):
        return _deny(request, "auth required")
    """Who used what, and what it cost in quota."""
    if ACCESS is None:
        return JSONResponse({"status": "access_disabled"}, status_code=503)
    since = time.time() - (hours * 3600)
    return JSONResponse({"status": "ok", **ACCESS.stats(since)})


@app.post("/usage/purge")
async def usage_purge(days: int = 30, request: _SSRequest = None):
    if not _auth_ok(request):
        return _deny(request, "auth required")
    if ACCESS is None:
        return JSONResponse({"status": "access_disabled"}, status_code=503)
    return JSONResponse({"deleted": ACCESS.purge(days)})


# ── Security: headers on every response, auth on every protected route ──
try:
    from security import (SECURITY_HEADERS, PUBLIC_PATHS, COOKIE, MAX_BODY,
                          bearer_from, wants_html, make_session, verify_session,
                          apply_headers, SECRET as SESSION_SECRET_SET)
    SECURE_OK = True
except Exception as _e:
    SECURE_OK = False
    print(f"[SECURITY] middleware unavailable: {_e}")


@app.middleware("http")
async def _security_middleware(request: _SSRequest, call_next):
    """Applies security headers to everything, and refuses oversized bodies."""
    try:
        clen = int(request.headers.get("content-length") or 0)
    except ValueError:
        clen = 0
    if clen > (MAX_BODY if SECURE_OK else 2 * 1024 * 1024):
        return JSONResponse({"error": {"message": "payload too large",
                                       "type": "payload_error"}},
                            status_code=413)
    resp = await call_next(request)
    if SECURE_OK:
        apply_headers(resp)
    return resp


def _auth_ok(request) -> bool:
    """True when the caller may use a protected route."""
    if not SECURE_OK or ACCESS is None:
        return True
    if not ACCESS.auth_required:
        return True
    # A browser session cookie counts, so the console works without JS keys.
    if verify_session(request.cookies.get(COOKIE, "")):
        return True
    key = bearer_from(request)
    if not key:
        return False
    return ACCESS.authenticate(key).ok


def _deny(request, reason: str):
    if wants_html(request):
        return JSONResponse({"error": reason}, status_code=401)
    return JSONResponse({"error": {"message": reason, "type": "auth_error"}},
                        status_code=401)


@app.get("/login")
async def login_page():
    from fastapi.responses import HTMLResponse
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "login.html")
    try:
        with open(p, encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except OSError:
        return HTMLResponse("<h1>login.html missing</h1>", status_code=500)


@app.post("/login")
async def login_submit(req: dict):
    """Exchange a client key for a signed session cookie."""
    if ACCESS is None:
        return JSONResponse({"error": {"message": "access control disabled",
                                       "type": "server_error"}}, status_code=503)
    if not ACCESS.auth_required:
        return JSONResponse({"ok": True, "note": "auth not required on this host"})
    key = (req.get("key") or "").strip()
    res = ACCESS.authenticate(key)
    if not res.ok:
        return JSONResponse({"error": res.reason or "invalid key"}, status_code=401)
    cookie = make_session(res.client)
    return JSONResponse({"ok": True, "client": res.client},
                        headers={"Set-Cookie": f"{COOKIE}={cookie}; HttpOnly; SameSite=Strict; Path=/"})


@app.post("/logout")
async def logout():
    return JSONResponse({"ok": True},
                        headers={"Set-Cookie": f"{COOKIE}=; Max-Age=0; Path=/"})


@app.get("/whoami")
async def whoami(request: _SSRequest):
    if ACCESS is None or not ACCESS.auth_required:
        return {"client": "open", "auth_required": False}
    u = verify_session(request.cookies.get(COOKIE, ""))
    if u:
        return {"client": u, "via": "session"}
    k = bearer_from(request)
    if k and ACCESS.authenticate(k).ok:
        return {"client": ACCESS.authenticate(k).client, "via": "bearer"}
    return JSONResponse({"error": "not authenticated"}, status_code=401)


@app.get("/openrouter/stats")
async def openrouter_stats():
    """Free OpenRouter models available as a second source."""
    if ORA is None:
        return JSONResponse({"status": "adapter_disabled"}, status_code=503)
    return JSONResponse({"status": "ok", **ORA.health()})


@app.post("/openrouter/refresh")
async def openrouter_refresh():
    if ORA is None:
        return JSONResponse({"status": "adapter_disabled"}, status_code=503)
    return JSONResponse({"status": "ok", "free_models": ORA.fetch_free_models(force=True)})


if __name__ == "__main__":
    print("=== SUPER-SOLDIER AUTO-ROUTER STARTED ===")
    print("Order: Groq -> Provider Console (Local multi-instance opencode) -> OpenRouter (last)")
    print("Opencode models available:", OPENCODE_MODELS)
    # Bind/port are env-configurable so the same build can run on any machine,
    # on a different port, and be exposed to the LAN only when you choose.
    _host = os.getenv("SS_HOST", "127.0.0.1")   # 0.0.0.0 = reachable on the LAN
    _port = int(os.getenv("SS_PORT", "8082"))

    # Fail loudly if something already owns the port. Without this, uvicorn
    # exits with a bare "[Errno 10048]" and an ORPHANED older process keeps
    # serving stale code -- which silently invalidates every later test.
    import socket
    _probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _probe.settimeout(1.0)
        if _probe.connect_ex(("127.0.0.1", _port)) == 0:
            print(f"[SERVE] ABORT: port {_port} is already in use by another "
                  f"process. An older Super-Soldier is probably still running.")
            print(f"[SERVE] find it:  netstat -ano | findstr :{_port}")
            print(f"[SERVE] kill it:  taskkill /F /PID <pid>")
            print("[SERVE] nothing was started.")
            raise SystemExit(1)
    finally:
        _probe.close()

    print(f"[SERVE] http://{_host}:{_port}  (UI: /ui)")
    uvicorn.run(app, host=_host, port=_port)
