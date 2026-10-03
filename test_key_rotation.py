"""Test key rotation + failover WITHOUT hitting Groq. Synthetic keys only.

Note: transient errors (timeout/5xx) must NOT bench a key. Only
key-specific errors (429/401/403) bench it. A single transient error with
one configured key must not kill subsequent requests.
"""
import sys, time, json
sys.path.insert(0, r"C:\Users\Bruce")
from key_rotation import KeyRotator

FAKE = {"GROQ_API_KEY": "gsk_fake000000000000000000000000000001",
        "GROQ_API_KEY_1": "gsk_fake000000000000000000000000000002",
        "GROQ_API_KEY_2": "gsk_fake000000000000000000000000000003"}

fails = []
def check(name, cond, detail=""):
    if not cond: fails.append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

print("=== 1. LOADS ALL KEYS, PRIMARY FIRST ===")
r = KeyRotator(FAKE)
check("3 keys loaded", len(r.slots) == 3, str([s.name for s in r.slots]))
check("primary is GROQ_API_KEY", r.slots[0].name == "GROQ_API_KEY")

print("\n=== 2. ROUND-ROBIN ===")
picked = [r.pick().name for _ in range(6)]
check("cycles through all keys", len(set(picked)) == 3, str(picked))

print("\n=== 3. HARD FAILURE (429) BENCHES + FAILS OVER ===")
r2 = KeyRotator(FAKE)
calls = []
def rate_limited(slot):
    calls.append(slot.name)
    if len(calls) == 1:
        slot.bench_now = True          # caller flags this as key-specific
        return False, "429"
    return True, f"ok via {slot.name}"
res, used = r2.call_with_failover(rate_limited)
check("failover succeeded", res is not None, str(res))
check("moved to a different key", used != calls[0], f"failed={calls[0]} used={used}")
check("hard-failed key benched", r2.slots[0].bench_until > 0)

print("\n=== 4. TRANSIENT FAILURE MUST NOT BENCH ===")
r3 = KeyRotator(FAKE)
calls = []
def flaky_network(slot):
    calls.append(slot.name)
    if len(calls) == 1:
        slot.bench_now = False         # transient timeout — do NOT bench
        return False, "ReadTimeout"
    return True, "recovered"
res3, used3 = r3.call_with_failover(flaky_network)
check("recovered on retry", res3 == "recovered", str(res3))
check("no key benched on transient error", all(s.bench_until == 0 for s in r3.slots))

print("\n=== 5. SINGLE KEY + TRANSIENT ERROR MUST SURVIVE ===")
r4 = KeyRotator({"GROQ_API_KEY": FAKE["GROQ_API_KEY"]})
calls = []
def net_blip(slot):
    calls.append(slot.name)
    if len(calls) == 1:
        return False, "ConnectionReset"     # bench_now defaults False
    return True, "ok"
res4, _ = r4.call_with_failover(net_blip)
check("single key still usable after blip", res4 == "ok", str(res4))
check("key NOT benched", r4.slots[0].bench_until == 0)

print("\n=== 6. EXCEPTION DOES NOT BENCH BY DEFAULT ===")
r5 = KeyRotator(FAKE)
def raises(slot):
    raise ConnectionError("down")
res5, _ = r5.call_with_failover(raises)
check("no exception propagated", res5 is None)
check("no key benched by bare exception", all(s.bench_until == 0 for s in r5.slots))
check("soft errors recorded", sum(s.soft_errors for s in r5.slots) >= 3,
      f"soft={sum(s.soft_errors for s in r5.slots)}")

print("\n=== 7. ALL HARD-FAIL -> None, no crash ===")
r6 = KeyRotator(FAKE)
def always_429(slot):
    slot.bench_now = True
    return False, "429"
res6, _ = r6.call_with_failover(always_429)
check("returns None", res6 is None)
check("all keys benched", all(s.bench_until > 0 for s in r6.slots))

print("\n=== 8. BACKOFF GROWS AND CAPS ===")
r7 = KeyRotator(FAKE)
slot = r7.slots[0]
delays = []
for _ in range(6):
    slot.bench_until = 0          # reset cooldown, keep cumulative failures
    now = time.time()
    slot.bench(now)
    delays.append(round(slot.bench_until - now))
check("grows then caps", delays[:3] == [60, 120, 240] and max(delays) == 900, str(delays))

print("\n=== 9. NO SECRETS LEAKED ===")
r8 = KeyRotator(FAKE)
blob = json.dumps(r8.stats())
check("no raw key in stats", not any(k in blob for k in FAKE.values()))
check("masked form shown", "..." in blob)

print("\n=== 10. EDGE CASES ===")
check("empty rotator safe", KeyRotator({}).call_with_failover(lambda s: (True,"x"))[0] is None)
check("zero-key stats", KeyRotator({}).stats()["keys_configured"] == 0)

print("\n" + "=" * 46)
print("RESULT:", "PASS — 100%" if not fails else f"FAIL — {fails}")