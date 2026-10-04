"""End-to-end browser test against BRUCE-DIGITAL using Playwright.

This is the check I skipped before. Simulating fetch() with curl is not the
same as a real browser session, and that gap is exactly how the cookie bug
survived a 17/17 API test run.

Uses the real console in a real browser: login -> ask -> read the answer ->
confirm the tag says which rung served it.
"""
import asyncio
import json
import os
import subprocess

HOST = "192.168.1.6"
TMP = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Temp")
OUT = r"C:\Users\Bruce\AppData\Local\Temp\bd_final.png"


def read_key(name):
    """Read a client's key from their ACCESS_KEY file in the share."""
    ps = f'''
$ErrorActionPreference = "Continue"
$p = "\\\\192.168.1.6\\Shared\\super-soldier\\ACCESS_KEY_{name}.txt"
if (Test-Path $p) {{
  $l = Get-Content $p | Where-Object {{ $_ -match "^KEY" }} | Select-Object -First 1
  if ($l) {{ Write-Output ($l -replace "^KEY\\s*:\\s*", "") }}
}}
'''
    s = os.path.join(TMP, f"rk_{name}.ps1")
    open(s, "w", encoding="utf-8").write(ps)
    r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", s], capture_output=True, timeout=200)
    os.remove(s)
    return (r.stdout or b"").decode("utf-8", errors="replace").strip()


KEY = read_key("SIMONE")
print("simone key loaded:", bool(KEY), f"({len(KEY)} chars)" if KEY else "")

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1280, "height": 1500})

        # ---- login ----
        await pg.goto(f"http://{HOST}:8082/login", wait_until="networkidle")
        check("login page loads", "SUPER-SOLDIER" in (await pg.content()).upper())
        await pg.fill("#k", KEY)
        await pg.click("button[type=submit]")
        await pg.wait_for_timeout(3500)
        check("logged in and reached the console", "/ui" in pg.url, pg.url)

        # ---- THE CRITICAL TEST: chat from a real browser ----
        console_errors = []
        pg.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

        await pg.wait_for_timeout(2000)
        cards = await pg.locator(".card").count()
        check("console rendered with stat cards", cards >= 4, f"{cards} cards")

        # free path
        await pg.fill("#msg", "What is 28 plus 28?")
        await pg.click("#send")
        await pg.wait_for_timeout(3000)
        free_txt = await pg.locator("#log .line").last.inner_text()
        check("browser chat WORKS (no 401)", "401" not in free_txt and "auth_error" not in free_txt,
              free_txt[:70])
        check("answer correct", "56" in free_txt, free_txt[:70])

        # LLM path
        await pg.fill("#msg", "Who wrote the play Hamlet?")
        await pg.click("#send")
        await pg.wait_for_timeout(6000)
        llm_txt = await pg.locator("#log .line").last.inner_text()
        check("LLM answer correct", "shakespeare" in llm_txt.lower(), llm_txt[:70])

        # cache path - repeat the same question
        await pg.fill("#msg", "Who wrote the play Hamlet?")
        await pg.click("#send")
        await pg.wait_for_timeout(3000)
        cache_txt = await pg.locator("#log .line").last.inner_text()
        check("repeat served free", "FREE" in cache_txt.upper(), cache_txt[:70])

        await pg.screenshot(path=OUT, full_page=True)
        check("no JS console errors", not console_errors, str(console_errors[:2]))
        await b.close()


asyncio.run(main())

passed = sum(1 for _, ok in results if ok)
print("\n" + "=" * 52)
print(f"RESULT: {passed}/{len(results)} passed")
print("STATUS:", "PASS — 100%" if passed == len(results) else "FAIL")
for n, ok in results:
    if not ok:
        print("  FAILED:", n)
print("screenshot:", OUT)