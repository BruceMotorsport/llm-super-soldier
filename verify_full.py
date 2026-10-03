"""FULL VERIFICATION — 100% pass or fail. No rounding."""
import json, urllib.request, time, ast

BASE = "http://127.0.0.1:8082"

def get(path):
    return json.load(urllib.request.urlopen(BASE + path, timeout=30))

def post(task, timeout=550):
    body = json.dumps({"task": task}).encode()
    req = urllib.request.Request(BASE + "/process", data=body,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))

results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

print("=== 1. HEALTH / ROUTER ===")
h = get("/health")
check("health ok", h.get("status") == "ok", str(h))
r = get("/router/status")
check("router status lists opencode models", len(r.get("opencode_models", [])) >= 2,
      str(r.get("opencode_models")))

print("\n=== 2. SINGLE-TASK ACCURACY ===")
cases = [
    ("What is the capital of Spain?", "madrid"),
    ("What is 12 times 12?", "144"),
    ("Who wrote Romeo and Juliet?", "shakespeare"),
]
for q, expect in cases:
    t = time.time()
    d = post(q)
    el = time.time() - t
    ans = str(d.get("result", "")).lower()
    check(f"answer contains '{expect}'", expect in ans, f"{el:.1f}s :: {ans[:52]}")

print("\n=== 3. MULTI-QUESTION BATCH ===")
qs = ["What is the capital of Japan?", "What is 5 times 5?", "What is the largest ocean?"]
t = time.time()
d = post("\n".join(qs))
el = time.time() - t
res = d.get("results", [])
check("3 results returned", len(res) == 3, f"got {len(res)}")
check("success flag true", d.get("success") is True)
check("no Unanswered", not any("Unanswered" in str(x.get("answer", "")) for x in res))
check("correct answers", "tokyo" in str(res[0].get("answer", "")).lower()
      and "25" in str(res[1].get("answer", "")) and "pacific" in str(res[2].get("answer", "")).lower())
check("latency < 60s", el < 60, f"{el:.1f}s")

print("\n=== 4. INTERREFERENCE ===")
d = post("What is the capital of France?\nWhat is question 1?")
res = d.get("results", [])
check("2 results", len(res) == 2, f"got {len(res)}")
check("Q2 resolves Q1 reference", "paris" in str(res[1].get("answer", "")).lower()
      if len(res) > 1 else False,
      str(res[1].get("answer", ""))[:60] if len(res) > 1 else "")

print("\n=== 5. CACHE ISOLATION (no bleed between batches) ===")
post("What is the capital of France?")
d2 = post("What is 7 times 8?")
check("fresh batch not answered from stale cache",
      "paris" not in str(d2.get("result", "")).lower()
      and "56" in str(d2.get("result", "")), str(d2.get("result", ""))[:50])

print("\n=== 6. LARGE BATCH ===")
qs = [f"What is {i} plus {i}?" for i in range(1, 41)]
t = time.time()
d = post("\n".join(qs))
el = time.time() - t
res = d.get("results", [])
check("40 results returned", len(res) == 40, f"got {len(res)}")
check("no Unanswered in 40", not any("Unanswered" in str(x.get("answer", "")) for x in res))
check("spot-check math correct",
      "2" in str(res[0].get("answer", "")) and "80" in str(res[39].get("answer", "")),
      f"q1={str(res[0].get('answer',''))[:20]} q40={str(res[39].get('answer',''))[:20]}")
check("latency < 120s", el < 120, f"{el:.1f}s")

print("\n=== 7. FILE INTEGRITY ===")
src = open(r"C:\Users\Bruce\llm_super_soldier_server_final.py", encoding="utf-8").read()
try:
    ast.parse(src); check("syntax valid", True)
except SyntaxError as e:
    check("syntax valid", False, str(e))
check("GROQ_URL points at Groq direct", 'GROQ_URL = "https://api.groq.com' in src)
check("single __main__ guard", src.count('if __name__ == "__main__":') == 1)
check("no duplicate process_large_batch", src.count("def process_large_batch") == 1)
check("no module-level uvicorn.run",
      len([l for l in src.split("\n") if "uvicorn.run(" in l and l.startswith(" ")]) == 1)
tree = ast.parse(src)
names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
check("no duplicate top-level defs", len(names) == len(set(names)), str(
    [n for n in set(names) if names.count(n) > 1]))

passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print(f"\n{'='*50}\nRESULT: {passed}/{total} passed")
print("STATUS:", "PASS — 100%" if passed == total else f"FAIL — {total-passed} failing")
failing = [n for n, ok, _ in results if not ok]
if failing:
    print("FAILING:", failing)