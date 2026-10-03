"""Security tests: start the server with auth ON and a body limit, then probe it.

Uses a temporary .env + access DB so the real config is never touched.
"""
import json, os, socket, subprocess, sys, time, urllib.error, urllib.request

ROOT = r"C:\Users\Bruce"
TMP  = os.path.join(ROOT, "AppData", "Local", "Temp", "ss_sec_test")
os.makedirs(TMP, exist_ok=True)
ENVF = os.path.join(TMP, ".env")
# Copy every GROQ_API_KEY* line verbatim from the real .env so the LLM test
# can actually run. Values are never printed.
_groq_lines = []
with open(os.path.join(ROOT, ".env"), encoding="utf-8") as f:
    for line in f:
        s = line.strip()
        if s.startswith("GROQ_API_KEY") and "=" in s:
            _groq_lines.append(s)

with open(ENVF, "w", encoding="utf-8") as f:
    f.write("SS_CLIENT_KEYS=bruce:SECRETBRUCE,luke:SECRETLUKE\n")
    for gl in _groq_lines:
        f.write(gl + "\n")
print(f"test env: {len(_groq_lines)} groq key line(s) copied (values not shown)")

PORT = 8099
BASE = f"http://127.0.0.1:{PORT}"
env = os.environ.copy()
env["SS_ENV_PATH"] = ENVF
env["SS_ACCESS_DB"] = os.path.join(TMP, "access.db")
env["SS_RATE_LIMIT"] = "5"
env["SS_SESSION_SECRET"] = "test-secret-not-production"
env["SS_MAX_BODY"] = "2048"
env["SS_REQUIRE_AUTH"] = "1"
env["SS_HOST"] = "127.0.0.1"
env["SS_PORT"] = str(PORT)

proc = subprocess.Popen([sys.executable, "llm_super_soldier_server_final.py"],
                        cwd=ROOT, env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

def req(path, method="GET", data=None, key=None, cookie=None, headers=None):
    h = dict(headers or {})
    if key:  h["Authorization"] = f"Bearer {key}"
    if cookie: h["Cookie"] = cookie
    body = json.dumps(data).encode() if data is not None else None
    if body is not None: h["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)
    except Exception as e:
        return 0, str(e).encode(), {}

fails = []
def check(name, cond, detail=""):
    if not cond: fails.append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

# wait for boot
ok = False
for _ in range(25):
    st, _, _ = req("/health")
    if st == 200: ok = True; break
    time.sleep(1)
print(f"server booted: {ok} (pid {proc.pid})")

if ok:
    print("=== 1. PUBLIC ENDPOINTS (must work without a key) ===")
    for p in ["/health", "/login"]:
        st, _, _ = req(p)
        check(f"{p} reachable", st == 200, f"HTTP {st}")

    print("\n=== 2. PROTECTED ENDPOINTS (must reject without a key) ===")
    for p in ["/router/status", "/v1/models", "/ladder/stats", "/keys/stats", "/usage"]:
        st, _, _ = req(p)
        check(f"{p} denies anonymous", st == 401, f"HTTP {st}")

    print("\n=== 3. PROTECTED ENDPOINTS (must accept a valid key) ===")
    for p in ["/router/status", "/v1/models", "/ladder/stats", "/keys/stats", "/usage"]:
        st, _, _ = req(p, key="SECRETBRUCE")
        check(f"{p} accepts valid key", st == 200, f"HTTP {st}")

    print("\n=== 4. WRONG KEY REJECTED ===")
    st, _, _ = req("/keys/stats", key="WRONGKEY")
    check("wrong key rejected", st == 401, f"HTTP {st}")

    print("\n=== 5. SECURITY HEADERS PRESENT ===")
    st, _, h = req("/health")
    hl = {k.lower(): v for k, v in h.items()}      # HTTP headers are case-insensitive
    for hdr in ["X-Content-Type-Options", "X-Frame-Options",
                "Content-Security-Policy", "Referrer-Policy"]:
        check(f"{hdr} set", hdr.lower() in hl, hl.get(hdr.lower(), "")[:40])

    print("\n=== 6. LOGIN ISSUES A SIGNED SESSION ===")
    st, body, h = req("/login", method="POST", data={"key": "SECRETBRUCE"})
    hl = {k.lower(): v for k, v in h.items()}
    raw_cookie = hl.get("set-cookie", "")
    cookie = raw_cookie.split(";")[0]
    check("login 200", st == 200, f"HTTP {st}")
    check("cookie issued", cookie.startswith("ss_session="), cookie[:24] + "...")
    check("cookie HttpOnly", "HttpOnly" in raw_cookie)
    check("cookie SameSite", "SameSite=Strict" in raw_cookie)

    print("\n=== 7. SESSION AUTHENTICATES PROTECTED ROUTES ===")
    st, _, _ = req("/keys/stats", cookie=cookie)
    check("session works on protected route", st == 200, f"HTTP {st}")

    print("\n=== 8. FORGED COOKIE REJECTED ===")
    forged = "ss_session=abc.def"
    st, _, _ = req("/keys/stats", cookie=forged)
    check("forged cookie rejected", st == 401, f"HTTP {st}")

    print("\n=== 9. NO SECRETS IN ANY RESPONSE ===")
    # A leak means the FULL key value appears. Masked prefixes like gsk_9T...4C
    # are intentional and safe.
    real = set()
    for line in _groq_lines:
        v = line.split("=", 1)[1].strip()
        if len(v) > 20:
            real.add(v.encode())
    leaked = []
    for p in ["/health", "/keys/stats", "/usage", "/login", "/router/status"]:
        st, body, _ = req(p, key="SECRETBRUCE")
        for rv in real:
            if rv in body:
                leaked.append((p, "groq key"))
        if b"SECRETBRUCE" in body:
            leaked.append((p, "client key"))
    check("no FULL keys in responses", not leaked, str(leaked))
    check("masked prefixes still shown (by design)",
          b"gsk_" in req("/keys/stats", key="SECRETBRUCE")[1])

    print("\n=== 10. BODY LIMIT ===")
    st, _, _ = req("/process", method="POST", data={"task": "x" * 5000}, key="SECRETBRUCE")
    check("oversized body rejected", st == 413, f"HTTP {st}")

proc.terminate()
try: proc.wait(timeout=10)
except Exception: proc.kill()

print("\n" + "=" * 50)
print("RESULT:", "PASS — 100%" if not fails else f"FAIL — {fails}")