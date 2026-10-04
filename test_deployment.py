"""Full deployment test from the Lenovo against BRUCE-DIGITAL.

Tests what a user would actually do, not just whether a port is open:
  1. every port answers /health
  2. :8085 direct serves a real answer
  3. the throttled model now fails over instead of erroring
  4. :8082 routes to Groq, OpenRouter, and the free ladder
  5. auth rejects anonymous and accepts a valid key
  6. the web console serves
"""
import json
import subprocess
import time

HOST = "192.168.1.6"
KEY = "BruceLocalKey2026"

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def curl(args, timeout=90):
    r = subprocess.run(["curl", "-s", "-m", str(timeout)] + args,
                       capture_output=True, timeout=timeout + 15)
    return (r.stdout or b"").decode("utf-8", errors="replace")


def get(path, port, key=None, timeout=30):
    a = [f"http://{HOST}:{port}{path}"]
    if key:
        a += ["-H", f"Authorization: Bearer {key}"]
    return curl(a, timeout)


def chat(port, model, msg, key=None, timeout=90):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": msg}]})
    a = ["-X", "POST", f"http://{HOST}:{port}/v1/chat/completions",
         "-H", "Content-Type: application/json", "-d", body]
    if key:
        a += ["-H", f"Authorization: Bearer {key}"]
    return curl(a, timeout)


print("=== 1. HEALTH ON ALL THREE PORTS ===")
for port, name in ((8082, "super-soldier"), (8085, "openrouter-free"),
                   (8086, "opencode-bridge")):
    out = get("/health", port)
    try:
        d = json.loads(out)
        check(f":{port} {name}", d.get("status") == "ok" and d.get("server") == name,
              out[:70])
    except Exception:
        check(f":{port} {name}", False, out[:70] or "no response")

print("\n=== 2. :8085 SERVES A REAL ANSWER ===")
out = chat(8085, "nvidia/nemotron-3.5-lightning:free",
           "What is the capital of Japan? One word.", timeout=60)
try:
    d = json.loads(out)
    txt = d["choices"][0]["message"]["content"]
    check("direct OpenRouter answer correct", "tokyo" in txt.lower(), txt[:50])
except Exception:
    check("direct OpenRouter answer correct", False, out[:120])

print("\n=== 3. 429 MODEL NOW FAILS OVER (was a hard error before) ===")
out = chat(8085, "qwen/qwen3.8-27b:free", "What is the capital of Peru? One word.",
           timeout=90)
try:
    d = json.loads(out)
    if "error" in d:
        check("throttled model recovered via another", False,
              f"still erroring: {d['error'].get('message')}")
    else:
        used = d.get("model")
        txt = d["choices"][0]["message"]["content"]
        check("throttled model recovered via another", True,
              f"answered by {used}: {txt[:32]}")
except Exception:
    check("throttled model recovered via another", False, out[:120])

print("\n=== 4. :8082 ROUTING ===")
out = chat(8082, "openai/gpt-oss-120b", "What is the capital of Norway?", key=KEY)
try:
    d = json.loads(out)
    sb = d["supersoldier"]
    txt = d["choices"][0]["message"]["content"]
    check("Groq route answers correctly", "oslo" in txt.lower(), txt[:40])
    check("served_by names groq", sb["served_by"].startswith("groq:"), sb["served_by"])
except Exception:
    check("Groq route answers correctly", False, out[:120])

out = chat(8082, "liquid/lfm-2.5-2.6b:free", "What is 11 times 11?", key=KEY)
try:
    d = json.loads(out)
    sb = d["supersoldier"]
    txt = d["choices"][0]["message"]["content"]
    check("OpenRouter route via :8082", "121" in txt, f"{sb['served_by']}: {txt[:30]}")
except Exception:
    check("OpenRouter route via :8082", False, out[:120])

out = chat(8082, "supersoldier", "What is 45 times 3?", key=KEY)
try:
    d = json.loads(out)
    sb = d["supersoldier"]
    txt = d["choices"][0]["message"]["content"]
    check("free ladder path (no LLM)", "135" in txt and sb["llm_called"] is False,
          f"{sb['served_by']}: {txt[:24]}")
except Exception:
    check("free ladder path (no LLM)", False, out[:120])

print("\n=== 5. AUTH ===")
code = curl(["-s", "-m", "20", "-o", "/dev/null", "-w", "%{http_code}",
             f"http://{HOST}:8082/v1/models"]).strip()
check("anonymous rejected on /v1/models", code == "401", f"HTTP {code}")
code = curl(["-s", "-m", "20", "-o", "/dev/null", "-w", "%{http_code}",
             "-H", f"Authorization: Bearer {KEY}",
             f"http://{HOST}:8082/v1/models"]).strip()
check("valid key accepted on /v1/models", code == "200", f"HTTP {code}")
code = curl(["-s", "-m", "20", "-o", "/dev/null", "-w", "%{http_code}",
             f"http://{HOST}:8082/health"]).strip()
check("/health public (needed for monitoring)", code == "200", f"HTTP {code}")

print("\n=== 6. WEB CONSOLE ===")
code = curl(["-s", "-m", "20", "-o", "/dev/null", "-w", "%{http_code}",
             f"http://{HOST}:8082/login"]).strip()
check("login page serves", code == "200", f"HTTP {code}")
body = curl(["-s", "-m", "20", f"http://{HOST}:8082/login"])
check("login page has real content", "SUPER-SOLDIER" in body.upper(),
      f"{len(body)} bytes")

print("\n=== 7. MODEL PICKER ===")
out = get("/v1/models", 8082, key=KEY)
try:
    d = json.loads(out)
    ids = [m["id"] for m in d["data"]]
    owners = {m["owned_by"] for m in d["data"]}
    check("models listed", len(ids) >= 10, f"{len(ids)} models")
    check("groq models present", "groq" in owners, str(sorted(owners)))
    check("openrouter models present", "openrouter" in owners, str(sorted(owners)))
except Exception:
    check("models listed", False, out[:120])

passed = sum(1 for _, ok in results if ok)
print("\n" + "=" * 52)
print(f"RESULT: {passed}/{len(results)} passed")
print("STATUS:", "PASS — 100%" if passed == len(results)
      else f"FAIL — {len(results)-passed} failing")
for n, ok in results:
    if not ok:
        print("  FAILED:", n)