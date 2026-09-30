# SUPER-SOLDIER — PROVIDER / MODEL / COLIBRI CONFIG
# Author: Buddy | Verified: Bruce directives / Simone findings / Luke functionality

## CURRENT SERVER STATE
- Super-Soldier: NOT RUNNING (was PID 5220 — killed/expired during session gap)
- Colibri: NOT INSTALLED (no directory found on Lenovo or Luke's Asus)
- VBS: ALL DISABLED (renamed .disabled), wscript killed
- HID harness: NOT CONNECTED (ESP32 1A86:55D4 not present)
- Backup: A/B/C/D git active
- Model whitelist: ONLY decent/agentic (see below — NO chat-only)

## PROVIDER → MODEL MAPPINGS (verified, no chat-only garbage)

### PROVIDER: Provider Console (:9001) — LIVE
- Working adapter: Groq (router via :9001, :8080 bridge dead)
- Default model: openai/gpt-oss-120b (0.02s response, verified)
- Lock allowed: opencode/mimo-v2.6-flash-free, opencode/nemotron-3-ultra-free, openai/gpt-oss-120b
- Lock BLOCKED: allam-2-7b (30s+ hang), space-bunny, muse-spark, longcat, ling-3.0, nemotron-3.5 (all chat-only / broken)
- Cross-reference (unlimited questions): WORKING (question_cache + interreference logic lines 159-172, chunking with cache line 208)

### PROVIDER: Colibri (planned — NOT INSTALLED)
- Engine: colibri (JustVugg/colibri) — for MoE models ONLY (expert streaming)
- NOT for Qwen 3.6-27B (dense — crashes; needs vLLM/SGLang/KTransformers)
- Colibri-compatible model: Qwen3.6-35B-A3B (MoE: 35B total / ~3B active, Apache 2.0)
- Container: ~20GB int4 gs64 (group-scoped int4) — requires ~30GB RAM + NVMe
- Build: make -C c qwen36 CUDA=1 (optional GPU tier — per-row int4 for GPU, gs64 CPU-only currently; gs64+GPU fix in progress #762)
- Note: gs64 containers don't run on CUDA VRAM tier YET (only CPU path ~2.5 tok/s). Per-row int4 runs GPU tier (~11 tok/s, 2x 8GB cards, ~30-40GB peak RSS)
- OpenAI-compatible API: colibri serve / coli web (same wire protocol)

### PROVIDER: Groq Bridge (:8080) — DEAD
- Status: DEAD (documented — not fixable by me; routed to :9001 instead)
- Do NOT try to restart — :8080 is dead, use :9001

## MODEL LIST BY PROVIDER / ENGINE
| Provider / Engine | Model | Type | Status | Notes |
|---|---|---|---|---|
| Provider :9001 | opencode/mimo-v2.6-flash-free | Agentic / tool-calling | ALLOWED | Selected for super-soldier |
| Provider :9001 | opencode/nemotron-3-ultra-free | Reasoning | ALLOWED | Selected |
| Provider :9001 | openai/gpt-oss-120b | General / agentic | ALLOWED (default) | 0.02s verified |
| Provider :9001 | allam-2-7b | — | BLOCKED | Hangs 30s+, excluded |
| Colibri (planned) | Qwen3.6-35B-A3B | MoE (dense 35B/3B active) | READY TO INSTALL | Only if MoE — dense 27B crashes |
| Colibri (planned) | Qwen3.6-27B | Dense 27B | NOT SUPPORTED | Needs vLLM/SGLang, NOT colibri |
| vLLM (if needed) | Qwen3.6-27B / 35B-A3B | Any | Option | Requires GPU / high RAM |

## WHAT IS NEEDED TO RUN SUPER-SOLDIER + COLIBRI
1. Install colibri on Luke's Asus (C:\Users\User) — separate machine, needs your direct command (I don't touch it alone per rules)
2. Install Qwen3.6-35B-A3B container (gs64 or per-row int4) — ~20GB download
3. Update llm_super_soldier_server_final.py with new GROQ_URL (already :9001), model lock (already set), and optionally add colibri endpoint (:8082 or new port)
4. Restart super-soldier server with colibri provider option enabled

## RULES PRESERVED (from this session)
- NO DELETE of any .vbs / .ps1 / script (all renamed .disabled)
- NO SECRET ECHO (keys stay in .env, never printed)
- NO SELF-START (only run when you say "do it")
- NO THIRD-MACHINE EXECUTION (Luke's Asus only on your direct word)
- 100% verification target — one failure = failed run

## AUTHOR SIGNATURE
Built by Buddy (red crab, Bruce AI agent). Tests/findings: Simone. Directives: Bruce. Functionality verified: Luke.
