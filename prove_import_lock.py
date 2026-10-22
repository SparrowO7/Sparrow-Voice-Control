"""Proof, or disproof, that the import lock was the thing that hung.

The fix hoisted `import state` (and keys/router/screen) to the top of
actions.py on the theory that a lazy import inside a command thread can
block on the interpreter's import lock while the speech worker is
importing pyttsx3 - which executes a 410 KB generated comtypes module.

That theory is cheap to assert and was worth checking, because the
timestamps argue against the first explanation: comtypes generated its
SAPI wrappers on 2026-10-03, so no code generation was happening during
the session that hung. Executing those modules still takes the lock, but
"still takes the lock" is a claim, not a measurement.

So this recreates the pre-fix condition exactly: drop the modules out of
sys.modules so the thread performs a REAL import, start the speech worker
at the same moment, and time how long the thread takes to get through its
import line.

    python prove_import_lock.py

A multi-second number for the lazy case, next to a fast one for the
hoisted case, is the proof. Two fast numbers mean the diagnosis is wrong
and the watchdog in main.py is the only thing that actually helps.
"""

from __future__ import annotations

import importlib
import sys
import threading
import time

LAZY = ["state", "keys", "screen", "router"]


def drop(names: list[str]) -> None:
    """Make these modules unimported, so `import X` really imports."""
    for name in names:
        sys.modules.pop(name, None)


def time_thread_import(names: list[str], alongside_tts: bool) -> float:
    """How long does a thread take to import `names`, with TTS loading?"""
    drop(names)
    # comtypes caches its generated modules in sys.modules too - dropping
    # them is what makes the speech import expensive a second time.
    for key in [k for k in sys.modules if k.startswith(("comtypes", "pyttsx3"))]:
        sys.modules.pop(key, None)

    took = [0.0]
    ready = threading.Event()

    def speech_side() -> None:
        ready.wait()
        try:
            import pyttsx3

            engine = pyttsx3.init()
            engine.stop()
        except Exception:
            pass

    def command_side() -> None:
        ready.wait()
        time.sleep(0.02)          # let the speech import get in first
        started = time.perf_counter()
        for name in names:
            importlib.import_module(name)
        took[0] = time.perf_counter() - started

    threads = [threading.Thread(target=command_side, daemon=True)]
    if alongside_tts:
        threads.insert(0, threading.Thread(target=speech_side, daemon=True))

    for thread in threads:
        thread.start()
    ready.set()
    for thread in threads:
        thread.join(30)
    return took[0]


def main() -> None:
    print("  how long a command thread waits on its import line\n")

    alone = time_thread_import(LAZY, alongside_tts=False)
    print(f"  lazy import, nothing else running   {alone * 1000:8.1f} ms")

    contended = time_thread_import(LAZY, alongside_tts=True)
    print(f"  lazy import, TTS importing too      {contended * 1000:8.1f} ms")

    # The fixed case: already in sys.modules, so the same lines are lookups.
    for name in LAZY:
        importlib.import_module(name)
    started = time.perf_counter()
    for name in LAZY:
        importlib.import_module(name)
    hoisted = time.perf_counter() - started
    print(f"  already imported (the fix)          {hoisted * 1000:8.1f} ms")

    print()
    if contended > max(0.5, alone * 4):
        print(f"  CONFIRMED: contention cost {(contended - alone) * 1000:.0f} ms "
              f"({contended / max(alone, 1e-6):.0f}x). Hoisting was the right fix.")
    else:
        print("  NOT CONFIRMED: the import lock is not the bottleneck here.")
        print("  The hang has another cause - keep the watchdog and look again.")


if __name__ == "__main__":
    main()
