"""Test every free OpenRouter model so the whitelist is evidence-based."""
import sys, time
sys.path.insert(0, r"C:\Users\Bruce")
from openrouter_adapter import fetch_free_models, chat

Q = "What is the capital of Japan? Answer in one word."
CODE_Q = "Write a one-line python function to reverse a string."

models = fetch_free_models()
print(f"free models discovered: {len(models)}\n")
print(f"{'model':<48} {'sec':>6}  result")
print("-" * 84)

good, bad = [], []
for m in models:
    ans, by = chat(Q, model=m, max_tokens=1024, timeout=40)
    if ans:
        ok = "tokyo" in ans.lower()
        good.append(m)
        flag = "" if ok else "  [WRONG/UNSTABLE]"
        print(f"{m:<48} {0:>6.1f}  OK{flag}")
    else:
        bad.append((m, by))
        print(f"{m:<48} {'-':>6}  FAIL: {by}")

print("\n" + "=" * 84)
print(f"WORKING: {len(good)}/{len(models)}")
for m in good:
    print("   ", m)
print(f"\nNOT USABLE: {len(bad)}")
for m, why in bad:
    print(f"    {m}  ({why})")

# verify code capability on the best few
print("\n=== CODE TEST on top 3 ===")
for m in good[:3]:
    ans, _ = chat(CODE_Q, model=m, max_tokens=2048, timeout=40)
    has_code = bool(ans) and ("def " in ans or "s[::-1]" in ans or "reverse" in ans)
    print(f"{m:<48} code_ok={has_code}  {(ans or '')[:60]!r}")