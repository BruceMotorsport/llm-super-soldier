# MODUS OPERANDI — Persistence until verified complete (Bruce directive)
# Author: Buddy (Simone findings / Bruce directives / Luke verified)
# Applies to: all LLM + Python bot tasks, HID harness fixes, server updates, repo pushes

## RULE (hard — never skip)
When encountering any problem, error, interruption, or failure:
1. CONTINUE the task. Do NOT stop and ask for clarification unless life-or-death / security breach.
2. Diagnose using available tools (logs, file checks, network checks, git status).
3. Apply fix if known; try alternative if first fails.
4. TEST — verify the fix worked (run the code, check output, confirm state).
5. REPORT — brief confirmation of what was done, what passed, and any remaining gap (honest, not polished).
6. If not fully complete: repeat 2–5. Do NOT declare "done" until both LLM and Python confirm via CrossCheck.

## What this fixes
- Prevents "wait for user instructions" stalls (interruption pattern just seen)
- Prevents "stop and ask for clarification" loops (Luke's HID harness, openrouter problems)
- Enforces 100% verification (Bruce: "Success = 100%, not 99%")
- Keeps tasks going through intermittent failures (gateway, interrupted turns, missing files)

## Enforcement mechanism
- CrossCheck protocol (LLM + Python both confirm) embedded in llm_super_soldier_server_final.py
- EconomicalAssistant handles simple queries; LLM handles reasoning — neither stops early
- OpenRouter hardened (disabled by default); only Groq + Provider Console run
- All blocked/chat-only models excluded; only decent/agentic allowed

## Verification
- Server file: 25990 chars, 469 lines — CrossCheck integrated in multi-question and single-question returns
- Git repo: master pushed (2cce4be, beb5324, a2c3d58, bb15b62) — all changes verified by git status
- HID harness: Bluetooth drop fixed (battery UUID added, ble_gap_update_params removed)
- No secrets in chat; .env untouched; no delete (only .disabled rename)

Author: Buddy (red crab). Protocol active from 2026-10-02.
