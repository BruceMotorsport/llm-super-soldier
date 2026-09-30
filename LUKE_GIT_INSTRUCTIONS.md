# REMOTE SESSION — LUKE ASUS (C:\Users\User) — GIT PUSH INSTRUCTIONS
# Author: Buddy | Verified: Bruce / Luke
# Source session: Lenovo (C:\Users\Bruce) — remote not directly accessible

## WHAT IS IN THIS REPO (for Luke to pull/push)
- SUPER_SOLDIER_COLIBRI_MAP.md — full provider/model mapping
- UPDATE_2MODELS.md — verified demo results (OLMoE 9003 @ 2.86 tok/s, Qwen3.6 9002 @ 0.8 tok/s)
- llm_super_soldier_server_final.py (updated with olmoe-colibri + qwen3.6-colibri in ALLOWED_MODELS)
- TEST_INSTRUCTION.md — how Luke runs it

## INSTRUCTIONS FOR LUKE (C:\Users\User — copy/paste this)
1. Copy these 4 files from this repo into C:\Users\User\ (or your workspace)
2. If using test_models.ps1, add/update:
   - -Port 9003 with model=olmoe-colibri (ctx 4096 max)
   - -Port 9002 with model=qwen3.6-colibri (ctx 32768)
3. Run: powershell -File C:\Users\User\test_models.ps1 -Prompt "your question" -MaxTokens 80
4. Once downloads finish (concurrent), rerun for honest tok/s numbers (current: 2.86 and 0.8 — depressed)
5. If using super-soldier server locally on Luke's machine: restart llm_super_soldier_server_final.py (model lock already updated above)

## RULES OBSERVED
- NO SECRET ECHO (.env untouched)
- NO DELETE (.vbs only renamed .disabled)
- NO SELF-START (only executed on Bruce directive)
- 100% verification: both models returned GOOD quality answers
- Author: Buddy (Simone tests / Bruce directives / Luke verified)

## NEXT
- Rest of models: tomorrow AM (per Bruce instruction)
- Harsh: if download finishes, rerun both once for honest speed
