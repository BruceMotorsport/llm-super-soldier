"""Prove the three-server architecture: groq + opencode + openrouter, isolated.

Checks:
  1. All three servers are separate processes on separate ports
  2. Super-Soldier never imports peer code (HTTP only)
  3. Models from all three are selectable
  4. Requests actually reach the right server
  5. Killing a peer does NOT kill Super-Soldier
"""
import json, os, subprocess, sys, time, urllib.request, urllib.error

SS = "http://127.0.0.1:8082"
fails = []


def check(name, cond, detail=""):
    if not cond: fails.append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def get(url, timeout=15):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def post(model, q, timeout=90):
    b = json.dumps({"model": model, "messages": [{"role": "user", "content": q}]}).encode()
    r = urllib.request.Request(SS + "/v1/chat/completions", data=b,
                               headers={"Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return time.time() - t, json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        return time.time() - t, None, f"HTTP {e.code}"
    except Exception as e:
        return time.time() - t, None, type(e).__name__


print("=== 1. THREE SEPARATE SERVERS ===")
for name, port, ident in [("super-soldier", 8082, "super-soldier"),
                          ("openrouter", 8085, "openrouter-free"),
                          ("opencode", 8086, "opencode-bridge")]:
    try:
        h = get(f"http://127.0.0.1:{port}/health")
        check(f":{port} is {name}", h.get("server") == ident, str(h.get("server")))
    except Exception as e:
        check(f":{port} is {name}", False, type(e).__name__)

print("\n=== 2. ISOLATION: no peer code imported into Super-Soldier ===")
src = open(r"C:\Users\Bruce\llm_super_soldier_server_final.py", encoding="utf-8").read()
check("no 'import openrouter_adapter'", "import openrouter_adapter" not in src)
check("no 'import oc_server'", "import oc_server" not in src)
check("talks to peers over HTTP", "requests.post(" in src and "/v1/chat/completions" in src)
# separate files on disk = separate deployables
for f in ("or_server.py", "oc_server.py"):
    check(f"{f} exists as its own server", os.path.isfile(rf"C:\Users\Bruce\{f}"))

print("\n=== 3. MODELS SELECTABLE FROM ALL THREE ===")
models = get(SS + "/v1/models")["data"]
owners = {}
for m in models:
    owners.setdefault(m["owned_by"], []).append(m["id"])
for o in ("groq", "opencode", "openrouter"):
    check(f"{o} models present in picker", o in owners, f"{len(owners.get(o, []))} models")
print(f"       total selectable: {len(models)}")

print("\n=== 4. ROUTING REACHES THE RIGHT SERVER ===")
el, d, err = post("openai/gpt-oss-120b", "What is the capital of Norway?")
check("groq model answers", d is not None, err or f"{el:.2f}s")
if d:
    check("groq source reported", d["supersoldier"]["served_by"].startswith("groq:"),
          d["supersoldier"]["served_by"])

el, d, err = post("qwen/qwen3.8-27b:free", "Who invented the telescope? Name only.")
check("openrouter model answers", d is not None, err or f"{el:.2f}s")
if d:
    check("openrouter source reported", "qwen" in d["supersoldier"]["served_by"],
          d["supersoldier"]["served_by"])

# opencode will legitimately fail (no account credential) — the point is that
# the FAILURE IS CONTAINED and Super-Soldier survives it.
el, d, err = post("opencode/mimo-v2.6-flash-free", "Say OK", timeout=150)
print(f"       opencode call: {el:.1f}s -> {'answered' if d else ('contained failure: ' + str(err))}")
h = get(SS + "/health")
check("Super-Soldier alive after opencode failure", h.get("status") == "ok")

print("\n=== 5. KILLING A PEER DOES NOT KILL SUPER-SOLDIER ===")
p = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
oc_pid = None
for line in p.stdout.split("\n"):
    if ":8086" in line and "LISTENING" in line:
        oc_pid = line.split()[-1]
        break
if oc_pid:
    subprocess.run(["taskkill", "/F", "/PID", oc_pid], capture_output=True)
    time.sleep(3)
    try:
        h = get(SS + "/health")
        check("Super-Soldier healthy with opencode down", h.get("status") == "ok")
        peers = get(SS + "/peers?force=true")
        down = [x for x in peers["peers"] if not x["up"]]
        check("opencode reported down", any("8086" in x["url"] for x in down),
              f"down: {[x['url'] for x in down]}")
        el, d, err = post("openai/gpt-oss-120b", "What is the capital of Peru?")
        check("groq still works with a peer down", d is not None, err or f"{el:.2f}s")
    except Exception as e:
        check("Super-Soldier healthy with opencode down", False, type(e).__name__)
else:
    check("found opencode pid to kill", False, "could not locate")

print("\n" + "=" * 54)
print("RESULT:", "PASS — 100%" if not fails else f"FAIL — {fails}")