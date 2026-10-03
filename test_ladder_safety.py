"""SAFETY + HONESTY TESTS for the answer ladder.
Every one of these must FAIL to produce an answer (or refuse), never fabricate."""
import sys, os
sys.path.insert(0, r"C:\Users\Bruce")
from answer_ladder import AnswerLadder, PythonRung

print("=== 1. CODE EXECUTION MUST BE IMPOSSIBLE ===")
attacks = [
    "import os",
    "__import__('os').system('dir')",
    "open('C:/Windows/System32/config/SAM')",
    "eval('1+1')",
    "os.system('whoami')",
    "().__class__.__bases__[0].__subclasses__()",
    "().__class__.__mro__",
    "1 if __import__('os') else 2",
    "[x for x in dir()]",
    "print('hi')",
    "exit()",
    "quit()",
    "help",
    "lambda: 1",
]
p = PythonRung()
bad = []
for a in attacks:
    try:
        out = p.answer(a)
    except Exception as e:
        out = f"<EXCEPTION {type(e).__name__}>"
    status = "REFUSED" if out is None else f"ANSWERED -> {out!r}"
    if out is not None:
        bad.append((a, out))
    print(f"  {a[:46]:<48} {status}")

print()
print("VERDICT:", "PASS - all attacks refused" if not bad else f"FAIL - {len(bad)} answered!")

print("\n=== 2. NO FABRICATION ON UNKNOWABLE QUESTIONS ===")
ladder = AnswerLadder()
unknown = [
    "Who won the 2031 World Cup?",
    "What is the meaning of life?",
    "What is my bank balance?",
    "As of 2026, who is the CEO of Acme Corp?",
]
for u in unknown:
    r = ladder.try_answer(u)
    if r:
        print(f"  [SUSPECT] {u[:40]:<42} -> [{r['served_by']}] {r['answer'][:50]}")
    else:
        print(f"  [OK -> LLM] {u[:40]:<42} correctly declined")

print("\n=== 3. CACHE ROUND-TRIP ===")
ladder2 = AnswerLadder()
q1 = "what is 7 * 6"
first = ladder2.try_answer(q1)
print(f"  first call : {first}")
second = ladder2.try_answer(q1)
print(f"  second call: {second}")
ok_cache = second and second["served_by"] == "cache"
print("  cache serves repeat without recompute:", ok_cache)

print("\n=== 4. CACHE NORMALISATION (same question, different case/punct) ===")
ladder3 = AnswerLadder()
a = ladder3.try_answer("What Is 9 * 9")
b = ladder3.try_answer("what is 9*9")
print(f"  'What Is 9 * 9' -> {a}")
print(f"  'what is 9*9'    -> {b}")
print("  normalised to same key:", (a and b and a["answer"] == b["answer"]))

print("\n=== 5. BROKEN RUNG MUST NOT KILL THE LADDER ===")
ladder4 = AnswerLadder()
def explode(q):
    raise RuntimeError("rung is broken")
ladder4.mcp.enabled = True
ladder4.mcp.register(explode)
try:
    r = ladder4.try_answer("what is 3 * 3")
    print(f"  survived broken rung -> {r}")
    print("  PASS" if r and r["served_by"] == "python" else "  FAIL")
except Exception as e:
    print(f"  FAIL - ladder crashed: {type(e).__name__}: {e}")

print("\n=== 6. EMPTY / WHITESPACE INPUT ===")
for bad_in in ["", "   ", "\n\n", None]:
    try:
        r = ladder4.try_answer(bad_in) if bad_in is not None else None
        print(f"  {bad_in!r:<10} -> {r}")
    except Exception as e:
        print(f"  {bad_in!r:<10} -> raised {type(e).__name__}")