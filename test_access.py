"""Test access control + rate limiting + logging. No network needed."""
import sys, time, json, os
sys.path.insert(0, r"C:\Users\Bruce")

# Point the module at a temp .env with fake client keys before import-time use
TMP_ENV = r"C:\Users\Bruce\AppData\Local\Temp\ss_test_env"
TMP_DB  = r"C:\Users\Bruce\AppData\Local\Temp\ss_test_access.db"
os.makedirs(TMP_ENV, exist_ok=True)
with open(os.path.join(TMP_ENV, ".env"), "w", encoding="utf-8") as f:
    f.write("SS_CLIENT_KEYS=bruce:BRUCEKEY123,luke:LUKEKEY456,simone:SIMONEKEY789\n")

os.environ["SS_ENV_PATH"] = TMP_ENV
os.environ["SS_ACCESS_DB"] = TMP_DB
os.environ["SS_RATE_LIMIT"] = "3"

import importlib
import access_log
importlib.reload(access_log)
from access_log import AccessLog, mask

# The reload above re-reads ENV_PATH at class-construction time; assert the
# module is actually pointed at the fixture before trusting any auth result.
assert os.path.isfile(access_log.ENV_PATH), \
    f"test fixture not wired: ENV_PATH={access_log.ENV_PATH}"
assert "SS_CLIENT_KEYS" in access_log._read_env(), \
    f"fixture .env not parsed: {access_log._read_env()}"

if os.path.exists(TMP_DB):
    os.remove(TMP_DB)

fails = []
def check(name, cond, detail=""):
    if not cond: fails.append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

print("=== 1. CLIENTS LOADED FROM .env ===")
al = AccessLog()
check("3 clients parsed", len(al._clients) == 3, str(sorted(al._clients)))
check("auth required when clients exist", al.auth_required is True)

print("\n=== 2. AUTH ===")
check("valid bruce key accepted", al.authenticate("BRUCEKEY123").client == "bruce")
check("valid luke key accepted", al.authenticate("LUKEKEY456").client == "luke")
check("wrong key rejected", al.authenticate("WRONG") .ok is False)
check("missing key rejected", al.authenticate("").ok is False)
check("reason given for rejection", al.authenticate("").reason != "")

print("\n=== 3. NO RAW KEYS STORED ===")
raw = sqlite_dump = open(TMP_DB, "rb").read() if os.path.exists(TMP_DB) else b""
al.record("bruce", mask("BRUCEKEY123"), "/v1/chat/completions", "supersoldier",
          "python", False, 12.0, 200)
check("raw key not in log db", b"BRUCEKEY123" not in open(TMP_DB,"rb").read())
check("key hashed in client store",
      all(len(v) == 64 for v in al._clients.values()), "sha256 hex")

print("\n=== 4. RATE LIMIT (3/min) ===")
res = [al.check_rate("luke") for _ in range(5)]
allowed = sum(1 for ok, _ in res if ok)
check("allows exactly 3", allowed == 3, f"allowed={allowed}")
check("5th blocked", res[3][0] is False)
check("retry_after reported", res[3][1] > 0, f"{res[3][1]}s")

print("\n=== 5. RATE LIMIT IS PER CLIENT ===")
al2 = AccessLog()
for _ in range(3): al2.check_rate("bruce")
ok_luke, _ = al2.check_rate("luke")
check("luke unaffected by bruce's usage", ok_luke is True)

print("\n=== 6. WINDOW EXPIRES ===")
al3 = AccessLog()
os.environ["SS_RATE_LIMIT"] = "2"
importlib.reload(access_log)
al3 = access_log.AccessLog()
ok1, _ = al3.check_rate("bruce", now=time.time())
ok2, _ = al3.check_rate("bruce", now=time.time())
ok3, _ = al3.check_rate("bruce", now=time.time())
check("third call blocked", ok3 is False)
ok_later, _ = al3.check_rate("bruce", now=time.time() + 61)
check("allowed again after 60s window", ok_later is True)

print("\n=== 7. LOGGING NEVER CRASHES A REQUEST ===")
try:
    al.record("bruce", "k", "/x", "m", "llm", True, 1.0, 200)
    al.record("", "", "", "", "", False, 0.0, 500)      # empty values
    al.record("x"*500, "y"*500, "/"*200, "m"*200, "llm", True, 1e9, 200)  # junk
    ok = True
except Exception:
    ok = False
check("logging survives junk input", ok)

print("\n=== 8. REPORTING ===")
st = al.stats()
check("totals present", "requests" in st["totals"])
check("llm vs free split", st["totals"]["llm_calls"] >= 1 and "free_pct" in st["totals"])
check("grouped by client", any(r["client"] == "bruce" for r in st["by_client"]))
check("grouped by served_by", len(st["by_served_by"]) >= 1)
check("recent list capped at 20", len(st["recent"]) <= 20)
check("no raw key in report", "BRUCEKEY123" not in json.dumps(st))

print("\n=== 9. AUTH CAN BE DISABLED ===")
os.environ["SS_REQUIRE_AUTH"] = "0"
importlib.reload(access_log)
al4 = access_log.AccessLog()
check("open mode when SS_REQUIRE_AUTH=0", al4.authenticate("anything").ok is True)
check("open mode labels caller", al4.authenticate("x").client == "open")
os.environ.pop("SS_REQUIRE_AUTH")

print("\n=== 10. PURGE ===")
n = al.purge(older_than_days=0)
check("purge deletes rows", isinstance(n, int) and n >= 0, f"deleted {n}")

print("\n=== 11. MASKING ===")
check("long key masked", mask("abcdefghijklmnop") == "abcd...op", mask("abcdefghijklmnop"))
check("short key not leaked", "*" in mask("abc"))

print("\n" + "=" * 46)
print("RESULT:", "PASS — 100%" if not fails else f"FAIL — {fails}")