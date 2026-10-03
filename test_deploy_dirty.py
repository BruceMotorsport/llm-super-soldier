"""E2E: a DIRTY clone must deploy without losing local work.

Simulates Simone's situation exactly: a clone with local edits, while origin
has moved forward. Runs the real git-handling block from deploy_supersoldier.py.
"""
import os, subprocess, sys

WORK = r"C:\Users\Bruce\AppData\Local\Temp\e2e_work"
OK, BAD, WARN = "[OK]", "[!!]", "[--]"


def say(t, m):
    print(f"  {t} {m}", flush=True)


def run(*args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def git(d, *args):
    return run("git", "-C", d, *args)


def main():
    # --- the exact block from deploy_supersoldier.py ---
    stashed = False
    dirty = git(WORK, "status", "--porcelain")
    n_local = len(dirty.stdout.strip().splitlines())
    if n_local:
        say(WARN, f"{n_local} local change(s) found - stashing (nothing lost)")
        s = git(WORK, "stash", "push", "-u", "-m",
                "auto-stash by deploy_supersoldier")
        stashed = s.returncode == 0

    r = git(WORK, "pull", "--ff-only", "origin", "master")
    if r.returncode != 0:
        say(BAD, f"pull failed: {(r.stderr or r.stdout).strip()[:160]}")
        if stashed:
            say(WARN, f'    see them:  cd "{WORK}" && git stash list')
            say(WARN, f'    restore:   cd "{WORK}" && git stash pop')
        return 1
    say(OK, "pulled latest")

    if stashed:
        say(WARN, "your local changes were set aside, NOT merged.")
        print(f'       To bring them back:  cd "{WORK}" && git stash pop')
    # --- end block ---

    print("\n=== STATE AFTER ===")
    print("tip     :", git(WORK, "log", "--oneline", "-1").stdout.strip())
    dirty2 = git(WORK, "status", "--porcelain").stdout.strip()
    print("dirty   :", dirty2 if dirty2 else "clean")
    print("stash   :", git(WORK, "stash", "list").stdout.strip() or "EMPTY")

    stash = git(WORK, "stash", "show", "-p", "stash@{0}").stdout
    if "SIMONE LOCAL WORK" in stash:
        print("\nRESULT: PASS - her work is safely in the stash and the server updated")
        return 0
    print("\nRESULT: FAIL - her work is not in the stash!")
    return 1


sys.exit(main())