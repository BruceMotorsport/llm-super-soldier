"""Test _guard accepts BOTH a client key and a session cookie.

Reproduces the bug that shipped: /v1/models honoured the cookie but
/v1/chat/completions did not, so the console loaded and then 401'd.
"""
import asyncio
import os
import sys
import tempfile

TMPD = tempfile.mkdtemp(prefix="ss_guard_")
ENVF = os.path.join(TMPD, ".env")
with open(ENVF, "w", encoding="utf-8") as f:
    f.write("SS_CLIENT_KEYS=test:TESTKEY123\n")
os.environ["SS_ENV_PATH"] = ENVF
os.environ["SS_ACCESS_DB"] = os.path.join(TMPD, "a.db")
os.environ["SS_REQUIRE_AUTH"] = "1"
os.environ["SS_SESSION_SECRET"] = "unit-test-secret"

import shutil
SRC = r"C:\Users\Bruce\llm_super_soldier_server_final.py"
DST = os.path.join(TMPD, "srv.py")
shutil.copy(SRC, DST)

import importlib.util
spec = importlib.util.spec_from_file_location("srv", DST)
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)

fails = []
def check(name, cond, detail=""):
    if not cond: fails.append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


class FakeReq:
    def __init__(self, headers=None, cookies=None):
        self.headers = headers or {}
        self.cookies = cookies or {}


print("=== 1. CLIENT KEY IS ACCEPTED ===")
r = FakeReq(headers={"authorization": "Bearer TESTKEY123"})
ok, code, body = asyncio.run(srv._guard(r, "/v1/chat/completions"))
check("key auth passes", ok is True and code == 200, f"code={code}")

print("\n=== 2. SESSION COOKIE IS ACCEPTED (the bug) ===")
cookie = srv.make_session("test")
r2 = FakeReq(headers={}, cookies={srv.COOKIE: cookie})
ok2, code2, body2 = asyncio.run(srv._guard(r2, "/v1/chat/completions"))
check("cookie auth passes", ok2 is True, f"code={code2} body={body2}")
check("not a 401", code2 != 401, str(code2))

print("\n=== 3. NO CREDENTIAL AT ALL IS REJECTED ===")
r3 = FakeReq()
ok3, code3, body3 = asyncio.run(srv._guard(r3, "/v1/chat/completions"))
check("anonymous rejected", ok3 is False and code3 == 401, f"code={code3}")
check("says missing api key", "missing" in str(body3).lower(), str(body3))

print("\n=== 4. WRONG KEY IS REJECTED (even with a valid cookie) ===")
r4 = FakeReq(headers={"authorization": "Bearer WRONGKEY"},
             cookies={srv.COOKIE: cookie})
ok4, code4, _ = asyncio.run(srv._guard(r4, "/v1/chat/completions"))
check("wrong key rejected", ok4 is False and code4 == 401, f"code={code4}")

print("\n=== 5. FORGED COOKIE REJECTED ===")
forged = f"{cookie.split('.')[0]}.deadbeef"
r5 = FakeReq(cookies={srv.COOKIE: forged})
ok5, code5, _ = asyncio.run(srv._guard(r5, "/v1/chat/completions"))
check("forged cookie rejected", ok5 is False and code5 == 401, f"code={code5}")

print("\n=== 6. x-api-key HEADER ALSO WORKS ===")
r6 = FakeReq(headers={"x-api-key": "TESTKEY123"})
ok6, code6, _ = asyncio.run(srv._guard(r6, "/v1/chat/completions"))
check("x-api-key accepted", ok6 is True, f"code={code6}")

print("\n=== 7. _auth_ok ALSO ACCEPTS THE COOKIE (routes use it) ===")
check("_auth_ok cookie-aware",
      srv._auth_ok(r2) is True, str(srv._auth_ok(r2)))
check("_auth_ok rejects anonymous",
      srv._auth_ok(r3) is False, str(srv._auth_ok(r3)))

print("\n" + "=" * 50)
print("RESULT:", "PASS — 100%" if not fails else f"FAIL — {fails}")

shutil.rmtree(TMPD, ignore_errors=True)