#!/usr/bin/env python3
"""
Super-Soldier one-command deploy.
Author: Buddy | Directive: Bruce

Clones/pulls the repo, installs deps, checks keys, starts the server, and
then PROVES it works — health endpoint, a free ladder answer, and a real
LLM answer. Exits non-zero if any step genuinely fails.

Usage:
    python deploy_supersoldier.py              # deploy + start + verify
    python deploy_supersoldier.py --check      # verify a running install only
    python deploy_supersoldier.py --no-start   # install without starting

Keys stay machine-specific in .env — never in chat, never in the repo.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = "https://github.com/BruceMotorsport/llm-super-soldier.git"
PORT = 8082
BASE = f"http://127.0.0.1:{PORT}"
SERVER = "llm_super_soldier_server_final.py"
DEPS = ["fastapi", "uvicorn", "requests"]

OK, BAD, WARN = "[OK]", "[!!]", "[--]"


def say(tag, msg):
    print(f"  {tag} {msg}", flush=True)


def repo_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "llm-super-soldier")


def load_env(path: str) -> dict:
    env = os.environ.copy()
    if not os.path.isfile(path):
        return env
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def get(path: str, timeout: int = 10):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read())


def post(path: str, payload: dict, timeout: int = 90):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def wait_for_health(attempts: int = 30, delay: float = 2.0):
    """Uvicorn + imports can take a while on a cold machine. Don't guess."""
    for i in range(attempts):
        try:
            return get("/health", timeout=5)
        except Exception:
            time.sleep(delay)
    return None


def verify() -> bool:
    """Prove the install actually serves. Returns True only on a full pass."""
    print("\n=== VERIFY ===")
    all_ok = True

    h = wait_for_health()
    if not h:
        say(BAD, "server never became healthy — is it running?")
        return False
    say(OK, f"health: {h.get('status')} on :{h.get('port', PORT)}")

    # keys
    try:
        k = get("/keys/stats")
        n = k.get("keys_configured", 0)
        if n:
            say(OK, f"groq keys loaded: {n} ({k.get('total_calls',0)} calls so far)")
        else:
            say(WARN, "no Groq keys found — server runs but every answer needs network")
            all_ok = False
    except Exception as e:
        say(WARN, f"/keys/stats unavailable: {e}")

    # ladder must answer without touching the LLM
    try:
        d = post("/v1/chat/completions",
                 {"model": "supersoldier",
                  "messages": [{"role": "user", "content": "What is 12 * 8?"}]})
        sb = d.get("supersoldier", {})
        ans = d["choices"][0]["message"]["content"]
        if str(96) in ans:
            say(OK, f"answer ladder works (served_by={sb.get('served_by')}, llm_called={sb.get('llm_called')})")
        else:
            say(BAD, f"ladder returned wrong answer: {ans!r}")
            all_ok = False
    except Exception as e:
        say(BAD, f"ladder test failed: {e}")
        all_ok = False

    # The LLM path must be PROVEN, not assumed. A cached answer would make
    # this pass without ever calling Groq, so bypass the cache entirely.
    try:
        probe = f"What is the capital of France? (verify run {int(time.time())})"
        d = post("/v1/chat/completions",
                 {"model": "supersoldier",
                  "messages": [{"role": "user", "content": probe}]})
        sb = d.get("supersoldier", {})
        ans = d["choices"][0]["message"]["content"].lower()
        if "paris" not in ans:
            say(BAD, f"LLM returned wrong answer: {ans[:60]!r}")
            all_ok = False
        elif sb.get("llm_called") is not True:
            say(BAD, f"LLM path not exercised — served_by={sb.get('served_by')} "
                     f"(cache hit means this proves nothing)")
            all_ok = False
        else:
            say(OK, f"LLM path works (served_by={sb.get('served_by')}, verified uncached)")
    except Exception as e:
        say(BAD, f"LLM test failed: {e}")
        all_ok = False

    # UI + usage endpoints
    for ep in ("/ui", "/usage"):
        try:
            with urllib.request.urlopen(BASE + ep, timeout=10) as r:
                n = len(r.read())
            say(OK, f"{ep} serves {n} bytes")
        except Exception as e:
            say(WARN, f"{ep} failed: {e}")

    return all_ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify only, don't install")
    ap.add_argument("--no-start", action="store_true", help="install without starting")
    args = ap.parse_args()

    print("=== SUPER-SOLDIER DEPLOY ===")
    d = repo_dir()

    if args.check:
        sys.exit(0 if verify() else 1)

    # 1. source
    print("\n[1/4] Source")
    if os.path.isdir(os.path.join(d, ".git")):
        r = subprocess.run(["git", "-C", d, "pull", "--ff-only", "origin", "master"],
                           capture_output=True, text=True)
        say(OK if r.returncode == 0 else BAD, "pulled latest" if r.returncode == 0
            else f"pull failed: {r.stderr.strip()[:120]}")
        if r.returncode != 0:
            sys.exit(1)
    else:
        if os.path.isdir(d):
            shutil.rmtree(d)
        r = subprocess.run(["git", "clone", REPO, d], capture_output=True, text=True)
        if r.returncode != 0:
            say(BAD, f"clone failed: {r.stderr.strip()[:160]}")
            sys.exit(1)
        say(OK, f"cloned into {d}")

    # 2. deps
    print("\n[2/4] Dependencies")
    req = os.path.join(d, "requirements.txt")
    cmd = [sys.executable, "-m", "pip", "install", "-q"]
    cmd += (["-r", req] if os.path.isfile(req) else DEPS)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        say(BAD, f"pip install failed: {r.stderr.strip()[:200]}")
        sys.exit(1)
    say(OK, "installed " + (os.path.basename(req) if os.path.isfile(req) else ", ".join(DEPS)))

    # 3. keys
    print("\n[3/4] Keys (.env stays on this machine)")
    env_path = os.path.join(d, ".env")
    env = load_env(env_path)
    groq = sorted(k for k in env if k.startswith("GROQ_API_KEY") and env[k])
    if groq:
        say(OK, f"{len(groq)} Groq key(s) found (names only: {', '.join(groq)})")
    else:
        say(WARN, f"no Groq keys in {env_path}")
        print("       Create it with one key per line:")
        print("         GROQ_API_KEY=<key>")
        print("         GROQ_API_KEY_1=<key>")
        print("       Never commit .env — it is gitignored.")
        if args.no_start:
            sys.exit(1)
    clients = env.get("SS_CLIENT_KEYS", "")
    say(OK, f"client access: {'configured' if clients else 'open (localhost only)'}")

    if args.no_start:
        print("\n=== INSTALLED (not started, --no-start) ===")
        return

    # 4. run
    print("\n[4/4] Start + verify")
    if not os.path.isfile(os.path.join(d, SERVER)):
        say(BAD, f"{SERVER} missing from repo")
        sys.exit(1)

    proc = subprocess.Popen([sys.executable, SERVER], cwd=d, env=env)
    try:
        ok = verify()
    finally:
        if not ok:
            proc.terminate()
            print("\n=== DEPLOY FAILED — server stopped ===")
            sys.exit(1)

    print("\n=== DEPLOY SUCCESSFUL ===")
    print(f"  Console : {BASE}/ui")
    print(f"  Usage   : {BASE}/usage")
    print(f"  API base: {BASE}/v1")
    print(f"  PID     : {proc.pid}  (running in background)")
    print("\n  Client keys (optional, .env):")
    print("    SS_CLIENT_KEYS=bruce:...,luke:...")


if __name__ == "__main__":
    main()