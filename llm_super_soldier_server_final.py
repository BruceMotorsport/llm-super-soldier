# ── Load Lazarus Vault (durable identity + rules) ─────────────────────────
VAULT_PATH = r"C:\\Users\\Bruce\\Lazarus\\buddy2-Lazarus\\memories\\MEMORY.md"
VAULT_MEMORY = {}
try:
    with open(VAULT_PATH) as vf: VAULT_MEMORY["content"] = vf.read()
except: VAULT_MEMORY["content"] = ""

# --- Autonomous session DB (thousands of items, cross-turn) ---
import sqlite3
DB_PATH = r'C:\Users\Bruce\llm-super-soldier-tests.db'
SESSION_DB = sqlite3.connect(DB_PATH)
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
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

GROQ_URL = "http://127.0.0.1:8080/v1/chat/completions"
PROVIDER_URL = "http://127.0.0.1:9001/v1/chat/completions"  # Provider Console — routes to Local multi-instance opencode + Groq
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

GROQ_KEY = os.getenv("GROQ_API_KEY", "")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")

# ── Provider States (Circuit Breakers) ──────────────────────────
class ProviderState:
    def __init__(self, name):
        self.name = name; self.failures = 0; self.last_fail = 0; self.open = True
    def ok(self):
        self.failures = 0; self.open = True
    def fail(self):
        self.failures += 1; self.last_fail = time.time()
        if self.failures >= 3: self.open = False
    def can(self):
        if self.open: return True
        if time.time() - self.last_fail > 30: self.open = True; return True
        return False

groq_s = ProviderState("Groq")
provider_s = ProviderState("ProviderConsole")  # Local multi-instance opencode + Groq
openrouter_s = ProviderState("OpenRouter")

# ── Opencode Model List (from Provider Console) ─────────────────
OPENCODE_MODELS = [
    "opencode/mimo-v2.6-flash-free",     # Luke/Simone preferred
    "opencode/nemotron-3.5-lightning-free",
    "opencode/nemotron-3-ultra-free",
    "opencode/ling-3.0-flash-fin-free",
    "opencode/muse-spark-1.3-contributor-free",
    "opencode/longcat-2.5-preview-free",
    "opencode/space-bunny-free",
]

# ── FastAPI ───────────────────────────────────────────────────────
app = FastAPI(title="Super-Soldier Auto-Route")

# ── Auto-Route Logic ─────────────────────────────────────────────
# Order: Groq → Provider Console (opencode) → OpenRouter (only if others fail)
# When OpenRouter is maxed, provider_s takes over (opencode models available)

def call_provider(prompt: str, model: str) -> Optional[str]:
    """Route through Provider Console — handles Local opencode selection."""
    if not provider_s.can(): return None
    ALLOWED_FREE_MODELS = {"opencode/mimo-v2.6-flash-free","opencode/nemotron-3.5-lightning-free","opencode/nemotron-3-ultra-free","opencode/ling-3.0-flash-fin-free","opencode/muse-spark-1.3-contributor-free","opencode/longcat-2.5-preview-free","opencode/space-bunny-free"}
    selected_model = model if model in ALLOWED_FREE_MODELS else "opencode/ling-3.0-flash-fin-free"
    payload = {"model": selected_model,
               "messages": [{"role":"user","content":prompt}],
               "max_tokens": 500, "temperature": 0.1}
    try:
        resp = requests.post(PROVIDER_URL, json=payload, timeout=30)
        if resp.status_code == 200:
            provider_s.ok()
            return resp.json()["choices"][0]["message"]["content"].strip()[:500]
        else:
            provider_s.fail()
    except: provider_s.fail()
    return None

def call_groq(prompt: str, model: str) -> Optional[str]:
    if not GROQ_KEY or not groq_s.can(): return None
    payload = {"model": model or "allam-2-7b",
               "messages": [{"role":"user","content":prompt}],
               "max_tokens": 500, "temperature": 0.1}
    try:
        resp = requests.post(GROQ_URL, json=payload, timeout=30)
        if resp.status_code == 200:
            groq_s.ok()
            return resp.json()["choices"][0]["message"]["content"].strip()[:500]
        elif resp.status_code == 429:
            groq_s.fail(); time.sleep(2)
        else: groq_s.fail()
    except: groq_s.fail()
    return None

def call_openrouter(prompt: str, model: str) -> Optional[str]:
    if not OPENROUTER_KEY or not openrouter_s.can(): return None
    payload = {"model": model or "openrouter/free",
               "messages": [{"role":"user","content":prompt}],
               "max_tokens": 500, "temperature": 0.1}
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
        return "opencode/nemotron-3.5-lightning-free"  # Fast
    return "opencode/mimo-v2.6-flash-free"  # Default for Luke/Simone

# ── Full Auto-Route (no manual selection needed) ─────────────────
def process_auto(task: str, prefer_opencode: bool = False) -> Dict[str, Any]:
    start = time.time()
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
            result = call_groq(question, "allam-2-7b")
            provider = "groq"
            if not result:
                result = call_provider(question, "opencode/mimo-v2.6-flash-free")
                provider = "provider-console-opencode"
            if not result:
                result = call_openrouter(question, "openrouter/free")
                provider = "openrouter"
            results.append({"question": question, "answer": result or "Unanswered", "provider": provider})
        return {
            "task": task,
            "results": results,
            "provider": "multi-question-routine",
            "latency": time.time() - start,
            "success": True,
            "opencode_models_available": OPENCODE_MODELS
        }

    # 1. Try Groq (fastest for simple tasks)
    result = call_groq(task, "allam-2-7b")
    provider = "groq"
    
    # 2. If Groq fails OR task needs opencode capabilities → Provider Console
    if not result or prefer_opencode or any(k in task.lower() for k in ["agent","tool","function","code"]):
        opencode_model = pick_opencode_model(task)
        result = call_provider(task, opencode_model)
        if result: provider = "provider-console-opencode"
    
    # 3. If Provider Console fails → OpenRouter (last, protected by circuit breaker)
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
async def status():
    return JSONResponse({
        "groq_open": groq_s.open, "provider_open": provider_s.open, "openrouter_open": openrouter_s.open,
        "order": ["groq","provider-console-opencode","openrouter"],
        "opencode_models": OPENCODE_MODELS
    })

@app.post("/process")
async def process(req: dict):
    result = process_auto(req.get("task",""), prefer_opencode=req.get("prefer_opencode", False))
    return JSONResponse(content=result)


if __name__ == "__main__":
    print("=== SUPER-SOLDIER AUTO-ROUTER STARTED ===")
    print("Order: Groq → Provider Console (Local multi-instance opencode) → OpenRouter (last)")
    print("Opencode models available:", OPENCODE_MODELS)
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
uvicorn.run(app, host="127.0.0.1", port=8082)
