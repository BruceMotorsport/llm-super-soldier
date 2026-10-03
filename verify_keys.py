"""Verify each configured key independently. Masks all output; never prints secrets."""
import sys, time, requests
sys.path.insert(0, r"C:\Users\Bruce")
from key_rotation import KeyRotator
keys = {s.name: s.secret for s in KeyRotator().slots}
rot = KeyRotator()
print(f"keys in .env: {len(keys)}")
print()

URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-120b"

print(f"{'key name':<20} {'result':<10} {'ms':>6}  detail")
print("-" * 72)

working, broken, limited = [], [], []
for slot in rot.slots:
    t = time.time()
    try:
        r = requests.post(URL, headers={"Authorization": f"Bearer {slot.secret}"},
                          json={"model": MODEL,
                                "messages": [{"role": "user", "content": "Say OK"}],
                                "max_tokens": 8}, timeout=30)
        ms = int((time.time() - t) * 1000)
        if r.status_code == 200:
            working.append(slot.name)
            print(f"{slot.name:<20} {'WORKING':<10} {ms:>6}  200 OK")
        elif r.status_code == 429:
            limited.append(slot.name)
            rem = r.headers.get("x-ratelimit-remaining-requests", "?")
            print(f"{slot.name:<20} {'RATE-LIMIT':<10} {ms:>6}  429 (remaining={rem})")
        elif r.status_code == 401:
            broken.append(slot.name)
            print(f"{slot.name:<20} {'INVALID':<10} {ms:>6}  401 bad key")
        elif r.status_code == 403:
            broken.append(slot.name)
            print(f"{slot.name:<20} {'BLOCKED':<10} {ms:>6}  403 project blocked")
        else:
            print(f"{slot.name:<20} {'ERROR':<10} {ms:>6}  HTTP {r.status_code}")
    except Exception as e:
        print(f"{slot.name:<20} {'TIMEOUT':<10} {'':>6}  {type(e).__name__}")

print()
print("=" * 72)
print(f"working : {len(working)}/{len(rot.slots)}  {working}")
print(f"limited : {len(limited)}  {limited}")
print(f"broken  : {len(broken)}  {broken}")

# Combined capacity estimate
if working or limited:
    ok = len(working) + len(limited)
    print()
    print(f"USABLE KEYS: {ok}")
    print(f"approx request capacity: {ok * 1000:,} per 5h "
          f"(assuming 1000 req/5h per free key)")

# Do the keys reach more models than one key alone?
print()
print("=== MODEL ACCESS ACROSS KEYS ===")
for m in ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]:
    codes = []
    for slot in rot.slots:
        try:
            r = requests.post(URL, headers={"Authorization": f"Bearer {slot.secret}"},
                              json={"model": m,
                                    "messages": [{"role": "user", "content": "hi"}],
                                    "max_tokens": 4}, timeout=25)
            codes.append(r.status_code)
        except Exception:
            codes.append("TO")
    reachable = codes.count(200)
    print(f"  {m:<26} reachable by {reachable}/{len(rot.slots)} key(s)  codes={codes}")