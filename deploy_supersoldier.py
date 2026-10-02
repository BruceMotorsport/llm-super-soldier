#!/usr/bin/env python3
"""
Super-Soldier One-Command Deploy
Author: Buddy | Directive: Bruce (deploy to all machines incl. Simone)
Usage: python deploy_supersoldier.py
Works on any Windows machine: pulls repo, installs deps, checks .env, starts server on :8082.
"""
import subprocess, os, sys, time

REPO = "https://github.com/BruceMotorsport/llm-super-soldier.git"
PORT = 8082

print("=== SUPER-SOLDIER DEPLOY (author: Buddy) ===")

# 1. Pull or clone
workdir = os.path.expanduser("~")
repo_dir = os.path.join(workdir, "llm-super-soldier")
if os.path.isdir(os.path.join(repo_dir, ".git")):
    print("[1/5] Pulling latest...")
    subprocess.run(["git", "-C", repo_dir, "pull", "origin", "master"], check=True)
else:
    print("[1/5] Cloning repo...")
    subprocess.run(["git", "clone", REPO, repo_dir], check=True)

# 2. Install dependencies
print("[2/5] Installing dependencies (fastapi, uvicorn, requests)...")
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "fastapi", "uvicorn", "requests"], check=True)

# 3. Check .env for keys
env_path = os.path.join(repo_dir, ".env")
if not os.path.exists(env_path):
    print(f"[3/5] No .env found at {env_path}")
    print("      Create it with: GROQ_API_KEY=<your-key>")
    print("      (keys stay machine-specific — never in chat or repo)")
else:
    print("[3/5] .env present — keys machine-specific (OK)")

# 4. Load .env into env vars
env = os.environ.copy()
if os.path.exists(env_path):
    for line in open(env_path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()

# 5. Start server
print("[4/5] Starting Super-Soldier server on 127.0.0.1:8082 ...")
proc = subprocess.Popen([sys.executable, "llm_super_soldier_server_final.py"],
                        cwd=repo_dir, env=env)
time.sleep(4)

# 5. Health check
import urllib.request, json
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=5) as r:
        health = json.loads(r.read())
    print(f"[5/5] HEALTH: {health}")
    print("=== DEPLOY SUCCESSFUL — Super-Soldier running on :8082 ===")
    print("Provider model: opencode/mimo-v2.6-flash-free (agentic)")
    print("Context: 32768 tokens | CrossCheck: active | OpenRouter: disabled by default")
except Exception as e:
    print(f"[5/5] HEALTH CHECK FAILED: {e}")
    print("Server process may need GROQ_API_KEY in .env — check [OPENROUTER]/[CROSS-CHECK] log lines")
    sys.exit(1)
