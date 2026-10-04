"""Test live .env reload: edit a config value while running, no restart."""
import importlib.util
import os
import sys
import tempfile
import time

# Isolated env dir so the test never touches the real .env
TMPD = tempfile.mkdtemp(prefix="ss_dotenv_")
ENVF = os.path.join(TMPD, ".env")
os.environ["SS_ENV_PATH"] = ENVF
os.environ["SS_ACCESS_DB"] = os.path.join(TMPD, "a.db")
os.environ["SS_REQUIRE_AUTH"] = "0"

with open(ENVF, "w", encoding="utf-8") as f:
    f.write("SS_CLIENT_KEYS=test:ORIGINALKEY\nGROQ_API_KEY=x\n")

sys.path.insert(0, r"C:\Users\Bruce")
import access_log
importlib.reload(access_log)

# point the server module at the fixture by copying it beside the temp .env
import shutil
SRC = r"C:\Users\Bruce\llm_super_soldier_server_final.py"
DST = os.path.join(TMPD, "srv.py")
shutil.copy(SRC, DST)

spec = importlib.util.spec_from_file_location("srv", DST)
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)

fails = []
def check(name, cond, detail=""):
    if not cond: fails.append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

print("=== 1. loads on first call ===")
ok = srv._load_dotenv(force=True)
check("applied .env", ok)
check("SS_CLIENT_KEYS in environment", "SS_CLIENT_KEYS" in os.environ,
      os.environ.get("SS_CLIENT_KEYS", "")[:30])

print("\n=== 2. unchanged file -> no reload ===")
before = srv._dotenv_stamp
ok = srv._load_dotenv()
check("skipped when unchanged", ok is False and srv._dotenv_stamp == before)

print("\n=== 3. CHANGED file -> reloads ===")
time.sleep(0.05)
with open(ENVF, "w", encoding="utf-8") as f:
    f.write("SS_CLIENT_KEYS=test:ROTATEDKEY\nGROQ_API_KEY=x\nNEWVAR=hello\n")
ok = srv._load_dotenv()
check("reloaded after change", ok is True)
check("new key value applied", os.environ.get("SS_CLIENT_KEYS") == "test:ROTATEDKEY",
      os.environ.get("SS_CLIENT_KEYS", "")[:40])
check("brand new var added", os.environ.get("NEWVAR") == "hello")

print("\n=== 4. comments and blank lines ignored ===")
with open(ENVF, "w", encoding="utf-8") as f:
    f.write("# a comment\n\n  \nGOOD=1\nnot_an_assignment\nBAD LINE\n")
os.environ.pop("GOOD", None)
srv._load_dotenv()
check("GOOD parsed", os.environ.get("GOOD") == "1")
check("no bogus var created", "not_an_assignment" not in os.environ)

print("\n=== 5. missing .env does not crash ===")
saved = srv._DOTENV_PATH
srv._DOTENV_PATH = os.path.join(TMPD, "nope.env")
try:
    r = srv._load_dotenv(force=True)
    check("returns False, no exception", r is False)
except Exception as e:
    check("returns False, no exception", False, type(e).__name__)
srv._DOTENV_PATH = saved

print("\n" + "=" * 50)
print("RESULT:", "PASS — 100%" if not fails else f"FAIL — {fails}")

import shutil as _sh
_sh.rmtree(TMPD, ignore_errors=True)