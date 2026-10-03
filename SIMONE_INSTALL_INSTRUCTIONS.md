# SIMONE — SUPER-SOLDIER INSTALL INSTRUCTIONS
# Author: Buddy | Directive: Bruce
# Repo: https://github.com/BruceMotorsport/llm-super-soldier.git
# Commit: eeddd3 (verify with `git log -1` after pulling)
#
# READ THIS FIRST — two rules that matter:
#   1. NEVER put your Groq keys in chat, email, or this repo. They go in .env
#      on your machine only. If you paste a key anywhere, rotate it.
#   2. Ask Buddy before changing anything not listed here. Do not "improve" it.

---

## STEP 1 — PREREQUISTICTS

You need:
- Python 3.11 or newer  →  `python --version`
- Git                →  `git --version`

If Python is missing, install from python.org and tick "Add to PATH".

---

## STEP 2 — DEPLOY (one command)

```
python deploy_supersoldier.py
```

Do NOT hand-copy files. The script clones the repo, installs dependencies,
starts the server, and then **verifies it actually works**. It prints
`DEPLOY SUCCESSFUL` only if all of these pass:

- server healthy on :8082
- Groq keys loaded
- answer ladder answers correctly (and for free)
- LLM path answers "What is the capital of France?" correctly
- `/ui` and `/usage` serve

If it prints `DEPLOY FAILED`, the output tells you which check failed. Send
that output to Buddy — do not try to fix it yourself.

Useful flags:
- `python deploy_supersoldier.py --check`  → verify an existing install
- `python deploy_supersoldier.py --no-start` → install only, don't start

---

## STEP 3 — YOUR KEYS

The script will warn if it finds no keys. Create the file it names
(usually `C:\Users\<you>\llm-super-soldier\.env`):

```
GROQ_API_KEY=<your-key>
GROQ_API_KEY_1=<another-key>
```

One key per line, any number of them. They rotate automatically — more keys
just means more capacity (each free key gets its own 1000 requests / 5 hours).

Bruce has keys already provisioned. Ask him for a slot rather than minting
your own if you are unsure.

**Never commit `.env`.** It is gitignored. Confirm with:
```
git status
```
— `.env` must NOT appear in the output.

---

## STEP 4 — CHECK YOUR INSTALL

```
python verify_full.py
```

Expect:
```
RESULT: 23/23 passed
STATUS: PASS — 100%
```

Also worth running once:

```
python test_security.py        →  RESULT: PASS — 100%
python test_key_rotation.py    →  RESULT: PASS — 100%
python test_ladder_safety.py   →  VERDICT: PASS - all attacks refused
```

If any of these fail, stop and send the output to Buddy.

---

## STEP 5 — USE IT

| What | Where |
|---|---|
| Web console | `http://127.0.0.1:8082/ui` |
| Usage / traffic report | `http://127.0.0.1:8082/usage` |
| OpenAI-compatible base URL | `http://127.0.0.1:8082/v1` |
| Health | `http://127.0.0.1:8082/health` |

In the console, every answer is tagged with what answered it:

- `python` or `cache` → **free**, no API quota used
- `llm` → costs quota

Use it as an OpenAI-compatible endpoint from any tool by pointing the base
URL at `http://127.0.0.1:8082/v1` and model `supersoldier`.

Available models: `supersoldier`, `openai/gpt-oss-120b`,
`openai/gpt-oss-20b`, `qwen/qwen3.8-27b`, plus the opencode ones.

---

## STEP 6 — LET OTHERS IN (only when Bruce says so)

Do **not** do this on your own. When Bruce approves it, add to `.env`:

```
SS_CLIENT_KEYS=bruce:...,simone:...
SS_SESSION_SECRET=<long random string>
SS_HOST=0.0.0.0
```

Then restart. Auth switches on automatically and:
- every request is attributed to a named client
- each client is limited to 60 requests/minute
- `/ui` requires sign-in at `/login`
- `/usage` shows who used what

**Known limitation:** traffic is plain HTTP on the LAN. Fine at home, NOT
safe across the internet. If it ever needs to be reachable from outside the
house, tell Buddy — it needs TLS first.

---

## TROUBLESHOOTING

| Symptom | Do this |
|---|---|
| `DEPLOY FAILED` | Send the full output to Buddy |
| Port 8082 already in use | Close the other instance: `netstat -ano \| findstr :8082` then `taskkill /PID <pid> /F` |
| Health returns nothing | Server not running — rerun step 2 |
| LLM answers say "providers exhausted" | Check your keys are valid; ask Buddy to verify |
| Ladder says "no answer" | That is correct behaviour — it means an LLM was needed |
| `[LADDER] DISABLED` in log | `pip install -r requirements.txt` |

---

## WHAT IS IN THIS REPO

| File | Purpose |
|---|---|
| `llm_super_soldier_server_final.py` | the server |
| `answer_ladder.py` | answers without an LLM (cache/python/local/web/mcp) |
| `key_rotation.py` | multi-key rotation + failover |
| `access_log.py` | per-client auth, rate limit, usage log |
| `security.py` | headers, sessions, body limit |
| `ui/index.html` | web console |
| `ui/login.html` | sign-in page |
| `deploy_supersoldier.py` | one-command install + verify |
| `verify_full.py`, `test_*.py` | the test suites |

---

## RULES OBSERVED BY BUDDY

- NO SECRETS IN CHAT OR REPO — keys live in `.env` only
- NO SELF-START — services start only on Bruce's directive
- VERIFY BEFORE CLAIMING — a deploy is not done until the tests pass
- ASK BEFORE CHANGING — Simone reports findings; Buddy makes the changes