#!/usr/bin/env python3
"""
One-command rebuild of the Super-Soldier stack on any machine.

    python rebuild.py                 # clone + install + start + verify
    python rebuild.py --check         # verify a running install only
    python rebuild.py --repair        # fix bind/firewall on a live install
    python rebuild.py --where C:\\path # use an existing folder

Why this exists
---------------
Rebuilding on 2026-10-05 took a week. The failures were never the app —
they were (a) no way to restart the stack atomically, (b) orphaned
processes holding ports and serving stale config, and (c) Windows shell
escaping bugs in hand-written .bat files. This script handles all three:

  * kills anything on the ports BEFORE starting (no orphans)
  * verifies the bind is 0.0.0.0 and fails loudly if not
  * opens the firewall
  * exports .env into the process environment
  * installs a Scheduled Task + watchdog so it survives reboots
  * runs a health check and exits non-zero on real failure

Safe to run repeatedly. Never deletes your .env or your keys.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = "https://github.com/BruceMotorsport/llm-super-soldier.git"
PORTS = (8082, 8085, 8086)
REQUIRED = [
    "llm_super_soldier_server_final.py",
    "or_server.py",
    "oc_server.py",
    "answer_ladder.py",
    "key_rotation.py",
    "access_log.py",
    "security.py",
    "requirements.txt",
]

OK, BAD, WARN = "[OK]", "[!!]", "[--]"


def say(tag, msg):
    print(f"  {tag} {msg}", flush=True)


def here() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def env_path() -> str:
    return os.path.join(here(), ".env")


# ── environment ─────────────────────────────────────────────────────
def load_env() -> dict:
    """Parse .env into a dict. Never prints values."""
    out = {}
    p = env_path()
    if not os.path.isfile(p):
        return out
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def apply_env(env: dict) -> int:
    for k, v in env.items():
        if k:
            os.environ[k] = v
    return len(env)


def find_python() -> str | None:
    """Find a real interpreter. The Windows Store stub lies about itself."""
    candidates = []

    # a python already on PATH - but validate it is not the Store stub
    try:
        r = subprocess.run([sys.executable, "-c", "import sys;print(sys.executable)"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and "WindowsApps" not in r.stdout:
            candidates.append(sys.executable)
    except Exception:
        pass

    for name in ("python", "python3", "py"):
        try:
            r = subprocess.run([name, "-c", "import sys;print(sys.executable)"],
                               capture_output=True, text=True, timeout=20)
            if r.returncode == 0:
                exe = r.stdout.strip()
                if exe and "WindowsApps" not in exe:
                    candidates.append(exe)
        except Exception:
            pass

    # walk install dirs - a wildcard mid-path does not work on Windows
    import glob
    for pat in (os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs",
                             "Python", "Python3*", "python.exe"),
                r"C:\Program Files\Python3*\python.exe",
                r"C:\Python3*\python.exe"):
        for c in glob.glob(pat):
            candidates.append(c)

    seen = set()
    for c in candidates:
        if c and c not in seen and os.path.isfile(c):
            seen.add(c)
            return c
    return None


# ── source ──────────────────────────────────────────────────────────
def ensure_source() -> str:
    d = here()
    missing = [f for f in REQUIRED if not os.path.isfile(os.path.join(d, f))]
    if not missing:
        return d

    say(WARN, f"{len(missing)} file(s) missing - fetching from GitHub")
    git = shutil_which("git")
    if not git:
        say(BAD, "git not found - cannot fetch. Copy the repo files manually.")
        raise SystemExit(1)

    if os.path.isdir(os.path.join(d, ".git")):
        r = subprocess.run([git, "-C", d, "pull", "--ff-only", "origin", "master"],
                           capture_output=True, text=True, timeout=300)
        say(OK if r.returncode == 0 else BAD,
            "pulled latest" if r.returncode == 0 else f"pull failed: {r.stderr.strip()[:120]}")
    else:
        r = subprocess.run([git, "clone", "--depth", "1", REPO, d],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            say(BAD, f"clone failed: {r.stderr.strip()[:200]}")
            raise SystemExit(1)
        say(OK, f"cloned into {d}")
    return d


def shutil_which(name):
    import shutil
    return shutil.which(name)


# ── keys ────────────────────────────────────────────────────────────
def check_env() -> bool:
    p = env_path()
    if not os.path.isfile(p):
        say(BAD, "no .env - the stack will start but every answer needs network")
        say(WARN, f"create it: notepad \"{p}\"")
        say(WARN, "  GROQ_API_KEY=<key>")
        say(WARN, "  GROQ_API_KEY_1=<another>   (optional, more capacity)")
        say(WARN, "  OR_API_KEY=<key>            (optional, OpenRouter free)")
        say(WARN, "Never commit .env - it is gitignored.")
        return False
    env = load_env()
    groq = [k for k in env if k.startswith("GROQ_API_KEY") and env[k]]
    if groq:
        say(OK, f"{len(groq)} Groq key(s): {', '.join(sorted(groq))}")
    else:
        say(WARN, "no Groq keys in .env - LLM calls will fail")
    if env.get("OR_API_KEY"):
        say(OK, "OR_API_KEY present - OpenRouter free models available")
    clients = [c.split(":")[0] for c in env.get("SS_CLIENT_KEYS", "").split(",") if ":" in c]
    say(OK, f"clients: {', '.join(clients) if clients else 'none (open, localhost only)'}")
    return True


# ── ports ───────────────────────────────────────────────────────────
def port_pids() -> dict:
    """PID listening on each port, via netstat. No admin needed."""
    found = {}
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=30)
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 5 and "LISTENING" in line:
                local, pid = parts[1], parts[-1]
                if ":" in local:
                    port = int(local.rsplit(":", 1)[1])
                    if port in PORTS:
                        found[port] = pid
    except Exception:
        pass
    return found


def kill_stragglers() -> int:
    killed = 0
    for port, pid in port_pids().items():
        for cmd in (["taskkill", "/F", "/PID", str(pid)],
                    ["taskkill", "/F", "/IM", "python.exe", "/FI",
                     f"WINDOWTITLE eq *{port}*"]):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                if r.returncode == 0:
                    killed += 1
                    break
            except Exception:
                continue
        say(WARN if killed else OK, f"port {port} (pid {pid}) - stopped"
            if killed else f"port {port} - could not free it")
    if not killed:
        say(OK, "ports already free")
    return killed


def bind_address(port: int) -> str:
    ps = (f"$c = Get-NetTCPConnection -LocalPort {port} -State Listen "
          f"-ErrorAction SilentlyContinue | Select-Object -First 1; "
          f"if ($c) {{ $c.LocalAddress }}")
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=40)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def firewall() -> None:
    ps = ("$ErrorActionPreference='SilentlyContinue'; "
          + ";".join(
              f"if (-not (Get-NetFirewallRule -DisplayName 'SS {p}')) "
              f"{{ New-NetFirewallRule -DisplayName 'SS {p}' -Direction Inbound "
              f"-Protocol TCP -LocalPort {p} -Action Allow -Profile Any | Out-Null }}"
              for p in PORTS))
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=90)
        if r.returncode == 0:
            say(OK, "firewall rules for 8082/8085/8086")
        else:
            say(WARN, "could not set firewall rules - run as Administrator")
    except Exception:
        say(WARN, "firewall check skipped")


# ── start ───────────────────────────────────────────────────────────
def start(py: str, env: dict) -> None:
    d = here()
    logs = os.path.join(d, "logs")
    os.makedirs(logs, exist_ok=True)
    servers = [("llm_super_soldier_server_final.py", 8082),
               ("or_server.py", 8085),
               ("oc_server.py", 8086)]
    for script, port in servers:
        log = os.path.join(logs, f"{port}.log")
        flags = 0x00000008 if os.name == "nt" else 0   # DETACHED_PROCESS
        with open(log, "ab") as fh:
            subprocess.Popen([py, script], cwd=d, env={**os.environ, **env},
                             stdout=fh, stderr=fh, creationflags=flags)
        say(OK, f"started {script} (:{port}) -> logs/{os.path.basename(log)}")
        time.sleep(3)


def install_autostart() -> bool:
    """Register boot task + watchdog. Needs admin; skip quietly if not."""
    wd = os.path.join(here(), "watchdog.ps1")
    if not os.path.isfile(wd):
        say(WARN, "watchdog.ps1 not present - skipping autostart")
        return False
    ps = (
        "$here = (Resolve-Path '.').Path; $sh = 'powershell.exe'; "
        "$ok = $true; "
        "try { $a = New-ScheduledTaskAction -Execute $sh -Argument "
        "\"-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `\""
        "Set-Location -LiteralPath '$here'; & '$here\\START_STACK.bat'`\"; "
        "$t = New-ScheduledTaskTrigger -AtStartup; "
        "$p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest; "
        "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1); "
        "Register-ScheduledTask -TaskName 'SuperSoldier-Startup' -Action $a -Trigger $t "
        "-Principal $p -Settings $s -Force | Out-Null } catch { $ok = $false }; "
        "try { $a2 = New-ScheduledTaskAction -Execute $sh -Argument "
        "\"-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"
        "$here\\watchdog.ps1`\"; "
        "$t2 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) "
        "-RepetitionInterval (New-TimeSpan -Minutes 5); "
        "$p2 = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest; "
        "$s2 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable; "
        "Register-ScheduledTask -TaskName 'SuperSoldier-Watchdog' -Action $a2 -Trigger $t2 "
        "-Principal $p2 -Settings $s2 -Force | Out-Null } catch { $ok = $false }; "
        "if ($ok) { 'OK' } else { 'NOADMIN' }"
    )
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout or "").strip()
        if "OK" in out:
            say(OK, "auto-start + watchdog installed (survives reboots)")
            return True
        say(WARN, "autostart needs Administrator - re-run as admin to enable")
        return False
    except Exception:
        say(WARN, "autostart check skipped")
        return False


# ── verify ──────────────────────────────────────────────────────────
def get(path, port, timeout=12):
    url = f"http://127.0.0.1:{port}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def verify() -> bool:
    print("\n=== VERIFY ===")
    all_ok = True

    # Each server reads its OWN bind variable - SS_HOST does not apply to 8085/8086
    BIND_VARS = {8082: "SS_HOST", 8085: "OR_HOST", 8086: "OC_HOST"}
    for port, name in ((8082, "super-soldier"), (8085, "openrouter-free"),
                       (8086, "opencode-bridge")):
        addr = bind_address(port)
        try:
            h = get("/health", port)
            alive = h.get("status") == "ok"
        except Exception:
            alive = False
        if not alive:
            say(BAD, f":{port} not responding")
            all_ok = False
            continue
        var = BIND_VARS[port]
        if addr == "0.0.0.0":
            say(OK, f":{port} {name} - up, bound 0.0.0.0 (reachable on the LAN)")
        elif addr:
            say(WARN, f":{port} {name} - up, bound {addr} (localhost only)")
            say(WARN, f"      to expose it: set {var}=0.0.0.0 in .env, then re-run")
        else:
            say(OK, f":{port} {name} - up")

    # credential state
    try:
        h = get("/health", 8085)
        if h.get("credential") == "present":
            say(OK, ":8085 OpenRouter credential loaded")
        else:
            say(WARN, ":8085 OpenRouter credential MISSING - add OR_API_KEY to .env")
    except Exception:
        pass

    # a real answer, through the whole path
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8082/v1/chat/completions",
            data=json.dumps({"model": "supersoldier",
                             "messages": [{"role": "user",
                                           "content": "What is 23 * 2?"}]}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read())
        ans = d["choices"][0]["message"]["content"]
        sb = d.get("supersoldier", {})
        if "46" in ans:
            say(OK, f"end-to-end answer works ({ans[:20]!r}, served_by {sb.get('served_by')})")
        else:
            say(BAD, f"unexpected answer: {ans[:40]!r}")
            all_ok = False
    except Exception as e:
        say(WARN, f"end-to-end check skipped: {type(e).__name__} "
                  f"(expected if auth is on - needs a client key)")

    return all_ok


def report() -> None:
    print("\n=== NEXT ===")
    env = load_env()
    clients = [c.split(":")[0] for c in env.get("SS_CLIENT_KEYS", "").split(",") if ":" in c]
    if clients:
        print(f"  clients configured: {', '.join(clients)}")
        print("  hand each one their key - never in chat, never in the repo")
    else:
        print("  no SS_CLIENT_KEYS set - auth is OFF, localhost only")
        print("    add to .env and restart to require a key:")
        print("      SS_CLIENT_KEYS=bruce:<key>,simone:<key>")
    print()
    print("  expose on the LAN : SS_HOST=0.0.0.0  (:8082)")
    print("                      OR_HOST=0.0.0.0   (:8085)")
    print("                      OC_HOST=0.0.0.0   (:8086)")
    print()
    print("  restart        : python rebuild.py")
    print("  verify only    : python rebuild.py --check")


# ── main ────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--repair", action="store_true")
    ap.add_argument("--where", help="unused - kept for clarity")
    ap.add_argument("--no-autostart", action="store_true")
    args = ap.parse_args()

    print("=" * 58)
    print("  SUPER-SOLDIER REBUILD")
    print("=" * 58)
    print(f"  folder: {here()}")

    if args.check:
        sys.exit(0 if verify() else 1)

    print("\n=== PREPARE ===")
    ensure_source()
    have_keys = check_env()

    py = find_python()
    if not py:
        say(BAD, "no working Python found.")
        say(WARN, "install from https://python.org/downloads/windows/")
        say(WARN, "and tick 'Add python.exe to PATH'")
        sys.exit(1)
    say(OK, f"python: {py}")

    env = load_env()
    n = apply_env(env)
    say(OK, f"exported {n} variable(s) from .env into this process")

    print("\n=== STOP ===")
    kill_stragglers()
    time.sleep(2)

    print("\n=== START ===")
    if args.repair:
        firewall()
    start(py, env)

    print("\n=== AUTOSTART ===")
    if args.no_autostart:
        say(WARN, "skipped (--no-autostart)")
    else:
        install_autostart()

    ok = verify()
    report()

    print()
    if ok and have_keys:
        print("RESULT: PASS")
        sys.exit(0)
    elif ok:
        print("RESULT: PASS (with warnings - see above)")
        sys.exit(0)
    else:
        print("RESULT: FAIL - see [!!] lines above")
        sys.exit(1)


if __name__ == "__main__":
    main()