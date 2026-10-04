"""Test the 429 failover without hitting OpenRouter.

Mock requests.post so we control the status sequence. The mock must answer
for EVERY model in the candidate list, otherwise an unmapped model returns
the default 404 and stops the walk early (which is correct behaviour, but
makes for a misleading test).
"""
import sys
from unittest import mock

sys.path.insert(0, r"C:\Users\Bruce")
import or_server
import requests as rq

CALLS = []
fails = []


def check(name, cond, detail=""):
    if not cond: fails.append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


class FakeResp:
    def __init__(self, code, payload=None):
        self.status_code = code
        self._payload = payload or {}

    def json(self):
        return self._payload


def ok(text):
    return FakeResp(200, {"choices": [{"message": {"role": "assistant", "content": text}}]})


def make_fake(throttled=(), unauthorized=False, ok_text="Tokyo"):
    """Every model answers 200 except those listed in `throttled`."""
    def fake_post(url, headers=None, json=None, timeout=None, **kw):
        mdl = json["model"]
        CALLS.append(mdl)
        if unauthorized:
            return FakeResp(401)
        if mdl in throttled:
            return FakeResp(429)
        return ok(ok_text)
    return fake_post


MSGS = [{"role": "user", "content": "What is the capital of Japan?"}]
REQ = "qwen/qwen3.8-27b:free"

# every model the candidate list can produce
ALL = set(or_server.VERIFIED) | {REQ}

print("=== 1. REQUESTED MODEL THROTTLED -> FALLS THROUGH ===")
CALLS.clear()
with mock.patch.object(rq, "post", make_fake(throttled={REQ})):
    text, used, err = or_server.complete(MSGS, REQ)
check("returned an answer", text == "Tokyo", f"text={text!r}")
check("did NOT use the throttled model", used != REQ, f"used={used}")
check("tried the requested one first", CALLS and CALLS[0] == REQ, str(CALLS))
check("no error surfaced", err is None, str(err))

print("\n=== 2. TWO MODELS THROTTLED -> RECOVERS ON A LATER ONE ===")
CALLS.clear()
two = set(CALLS)  # placeholder, filled below
victims = {REQ, "cohere/north-mini-code:free"}
with mock.patch.object(rq, "post", make_fake(throttled=victims)):
    text, used, err = or_server.complete(MSGS, REQ)
check("recovered", text == "Tokyo", f"used={used}")
check("walked past both throttled models", len(CALLS) >= 3, f"calls={len(CALLS)}")
check("never returned a throttled model", used not in victims, f"used={used}")

print("\n=== 3. 401 -> FAIL FAST (one call only) ===")
CALLS.clear()
with mock.patch.object(rq, "post", make_fake(unauthorized=True)):
    text, used, err = or_server.complete(MSGS, REQ)
check("no answer", text is None)
check("reported http 401", err == "http 401", str(err))
check("exactly ONE call - no pointless retries", len(CALLS) == 1, f"calls={len(CALLS)}")

print("\n=== 4. SUCCESS ON FIRST TRY ===")
CALLS.clear()
with mock.patch.object(rq, "post", make_fake()):
    text, used, err = or_server.complete(MSGS, REQ)
check("honoured the requested model", used == REQ, str(used))
check("exactly one call", len(CALLS) == 1, f"calls={len(CALLS)}")

print("\n=== 5. EVERY MODEL THROTTLED -> HONEST FAILURE ===")
CALLS.clear()
with mock.patch.object(rq, "post", make_fake(throttled=ALL)):
    text, used, err = or_server.complete(MSGS, REQ)
check("no answer", text is None)
check("error says throttled", err == "throttled", str(err))
check("tried the whole candidate list", len(CALLS) == 5, f"calls={len(CALLS)}")

print("\n=== 6. NO CREDENTIAL -> no crash ===")
try:
    or_server._key_cache = None
    or_server.API_KEY_ENV = "definitely-not-a-real-key"
    text, used, err = or_server.complete(MSGS, REQ)
    check("handled missing/bad credential without raising", True, f"err={err}")
except Exception as e:
    check("handled missing/bad credential without raising", False, type(e).__name__)

print("\n=== 7. THROTTLE COUNTER EXPOSED ===")
st = or_server._stats
check("throttled counter exists", "throttled" in st, str(sorted(st.keys())))

print("\n" + "=" * 48)
print("RESULT:", "PASS — 100%" if not fails else f"FAIL — {fails}")