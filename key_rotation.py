"""
Multi-key Groq rotation with automatic failover.

Loads every GROQ_API_KEY* from .env, round-robins across healthy keys, and
fails over on 429 (rate limit) or transient errors. Keys that keep failing are
temporarily benched with a cooldown so a dead key doesn't stall every request.

Keys are never logged, printed, or returned by any endpoint — only masked
prefixes and counts are exposed.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, List, Optional

ENV_PATH = os.environ.get(
    "SS_ENV_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
)

# Benched keys stay out of rotation for this long, doubling per repeat failure
BENCH_BASE = int(os.environ.get("SS_KEY_BENCH_SECONDS", "60"))
BENCH_MAX = int(os.environ.get("SS_KEY_BENCH_MAX", "900"))
# Longest a single request will sleep waiting for a rate-limited key
WAIT_MAX = float(os.environ.get("SS_KEY_WAIT_MAX", "35"))


def _load_env_keys(env_path: str = ENV_PATH) -> Dict[str, str]:
    """Parse .env for GROQ_API_KEY* values. Never logs the values."""
    keys: Dict[str, str] = {}
    if not os.path.isfile(env_path):
        return keys
    try:
        with open(env_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                name = name.strip()
                value = value.strip().strip('"').strip("'")
                if name.startswith("GROQ_API_KEY") and value:
                    keys[name] = value
    except OSError:
        pass
    return keys


def _mask(key: str) -> str:
    """Masked identifier only — enough to tell keys apart, useless if leaked."""
    return f"{key[:6]}...{key[-2:]}" if len(key) > 12 else "***"


class KeySlot:
    def __init__(self, name: str, secret: str):
        self.name = name
        self.secret = secret
        self.bench_until = 0.0
        self.failures = 0
        self.calls = 0
        self.errors = 0
        self.soft_errors = 0
        # Set by the caller's attempt() to distinguish a bad key from a blip
        self.bench_now = False
        self.soft_error = None
        self.retry_after = 0.0   # provider's own reset hint, seconds

    def available(self, now: float) -> bool:
        return now >= self.bench_until

    def bench(self, now: float, retry_after: float = 0.0):
        """Take this key out of rotation. `retry_after` (seconds) comes from the
        provider's own rate-limit headers and wins over our backoff."""
        self.failures += 1
        delay = min(BENCH_BASE * (2 ** (self.failures - 1)), BENCH_MAX)
        if retry_after and 0 < retry_after <= BENCH_MAX:
            # The provider's own reset hint is authoritative — it knows when
            # the limit actually clears. Never inflate it with our backoff.
            delay = retry_after
        self.bench_until = now + delay

    def public(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "masked": _mask(self.secret),
            "failures": self.failures,
            "calls": self.calls,
            "errors": self.errors,
            "soft_errors": self.soft_errors,
            "benched": bool(self.bench_until and time.time() < self.bench_until),
        }


class KeyRotator:
    """Round-robin across healthy keys with failover + cooldown."""

    def __init__(self, keys: Optional[Dict[str, str]] = None):
        raw = keys if keys is not None else _load_env_keys()
        # GROQ_API_KEY is primary; numbered variants are additional capacity
        ordered = sorted(raw.items(), key=lambda kv: (kv[0] != "GROQ_API_KEY", kv[0]))
        self.slots: List[KeySlot] = [KeySlot(n, v) for n, v in ordered]
        self._idx = 0
        self._lock = threading.Lock()
        self.total_calls = 0
        self.total_failovers = 0

    # ── selection ──────────────────────────────────────────────────
    def pick(self, start_after: Optional[int] = None) -> Optional[KeySlot]:
        """Return the next available slot, or None if all are benched.

        Rotates: each call advances the cursor so consecutive picks walk the
        whole pool instead of sticking on slot 0.
        """
        now = time.time()
        with self._lock:
            n = len(self.slots)
            if n == 0:
                return None
            start = self._idx if start_after is None else start_after
            for step in range(n):
                i = (start + step) % n
                if self.slots[i].available(now):
                    self._idx = (i + 1) % n      # advance cursor past this slot
                    return self.slots[i]
        return None

    def call_with_failover(self, fn, tries: int = None):
        """
        Call fn(slot) until one succeeds. A slot 'succeeds' when fn returns a
        tuple (ok, result). A 429 or exception benches that key and moves on.

        Returns (result, slot_name) or (None, None) when every key failed.
        """
        slots = [s for s in self.slots]
        if not slots:
            return None, None
        tries = tries or len(slots)
        last_result = None
        start = self._idx
        transient_retries = 0
        attempt = 0
        # tries counts distinct keys; soft failures may consume extra attempts
        max_attempts = tries + 2
        while attempt < max_attempts:
            slot = self.pick(start_after=(start + attempt) if attempt == 0 else None)
            if slot is None:
                # Every key benched. With one key configured there is nothing to
                # fail over to, so a rate-limit must be WAITED OUT rather than
                # returned as a failed answer. Bounded so a request can't hang.
                now = time.time()
                waits = [s.bench_until - now for s in self.slots if s.bench_until > now]
                if not waits:
                    break
                shortest = min(waits)
                if transient_retries >= 3:
                    break
                if len(self.slots) > 1:
                    # multiple keys: try another instead of sleeping
                    if transient_retries >= 1:
                        break
                    time.sleep(min(shortest, 2.0))
                else:
                    # single key: wait it out (bounded by WAIT_MAX)
                    if shortest > WAIT_MAX:
                        break
                    time.sleep(min(shortest, WAIT_MAX))
                transient_retries += 1
                attempt += 1
                continue
            attempt += 1
            try:
                ok, result = fn(slot)
            except Exception as e:                      # network/transient
                if getattr(slot, "bench_now", False):
                    slot.bench(time.time(), getattr(slot, 'retry_after', 0.0))
                    slot.errors += 1
                else:
                    slot.soft_errors += 1
                last_result = e
                self.total_failovers += 1
                continue
            slot.calls += 1
            self.total_calls += 1
            if ok:
                return result, slot.name
            if getattr(slot, "bench_now", False):
                slot.bench(time.time(), getattr(slot, 'retry_after', 0.0))      # key-specific (429/401/403)
                slot.errors += 1
            else:
                slot.soft_errors += 1         # transient (timeout/5xx) — keep key
            last_result = result
            self.total_failovers += 1
        return None, None

    # ── introspection (no secrets) ─────────────────────────────────
    def stats(self) -> Dict[str, Any]:
        return {
            "keys_configured": len(self.slots),
            "total_calls": self.total_calls,
            "failovers": self.total_failovers,
            "keys": [s.public() for s in self.slots],
        }


if __name__ == "__main__":
    import json
    rot = KeyRotator()
    print(json.dumps(rot.stats(), indent=2))
    print("\nkey names found:", [s.name for s in rot.slots])