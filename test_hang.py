"""Does the session survive a stuck command? Run this, send me the output.

    python test_hang.py            # the check
    python test_hang.py --loud     # let it actually talk

Harsh hit this: command one worked, command two printed its intent line and
nothing ever followed.

    heard   "Open Chrome."
    intent  open_app  type=any  term="chrome"  via keywords  1ms
    <nothing, forever>

Two theories were written up and both were wrong, which is worth recording
so neither gets tried again:

  - comtypes generating its COM wrappers on first use and holding the import
    lock. Disproved by the file dates: the SAPI wrappers were generated on
    2026-10-03, so nothing was being generated during the session that hung.
  - the interpreter's import lock in general. Disproved by measurement in
    prove_import_lock.py: Python has had per-module locks since 3.3, and the
    contended case came back in 6 ms.

What IS certain, found by reading main.py rather than guessing: commands ran
while holding the `busy` lock, and both tap handlers did `if busy.locked():
return` with nothing printed. So one command that did not come back meant
every command after it was dropped in silence - which is the difference
between "slow" and "the session is dead", and it is what you were looking
at. That part is fixed; the four checks below are what prove it.

The last check deliberately wedges a command for longer than the session is
willing to wait. That is the one that used to kill everything after it.
"""

from __future__ import annotations

import sys
import threading
import time

import config

if "--loud" not in sys.argv:
    config.SPEAK_REPLIES = False

import actions   # noqa: E402  - after the config touch above
import main      # noqa: E402
import router    # noqa: E402
import speak     # noqa: E402

# A real launch here is well under a second. Anything near this is a hang.
LIMIT = 15.0

SEQUENCE = [
    # The first must produce a spoken reply - that is what starts the speech
    # worker, which every discarded theory blamed and which runs regardless.
    "open notepad",
    # The one that stopped dead.
    "open chrome",
    "minimize karo",
    "restore window",
]


def run_like_voice_mode(command: str, watchdog: bool = True) -> tuple[bool, float]:
    """Run one command in its own thread, the way voice mode does.

    The threading is the point. On the main thread every one of these
    finishes in milliseconds, which is exactly why the bug survived being
    "tested" - it only shows up with a command thread, a speech worker, and
    something holding the session's lock.
    """
    done = threading.Event()
    elapsed = [0.0]

    def work() -> None:
        started = time.perf_counter()
        guard = main._watchdog(command, seconds=4.0) if watchdog else None
        try:
            intent = router.route(command)
            result = actions.run(intent, raw_text=router.normalize(command))
            print(f"      -> ok={result.ok}  {result.say!r}")
        except Exception as exc:
            print(f"      -> EXC {type(exc).__name__}: {exc}")
        finally:
            if guard is not None:
                guard.cancel()
            elapsed[0] = time.perf_counter() - started
            # Speaking is what starts the TTS worker, so it stays in even
            # though the import theory it was here to test did not survive.
            speak.say("theek hai")
            done.set()

    threading.Thread(target=work, daemon=True).start()
    return done.wait(LIMIT), elapsed[0]


def survives_a_stuck_command() -> bool:
    """The real check: does a wedged command still kill everything after it?

    Deliberately holds the gate for longer than it is willing to wait, then
    asks whether the next command would be allowed through. Before the fix
    the answer was no, forever, and silently.
    """
    print("\n  wedging a command on purpose, then talking over it")
    gate = main.Gate(stuck_after=1.5)
    release = threading.Event()

    def stuck_command() -> None:
        gate.start()
        try:
            release.wait(10)
        finally:
            gate.finish()

    threading.Thread(target=stuck_command, daemon=True).start()
    time.sleep(0.3)

    # Mid-command: refusing is correct, but it has to SAY so.
    early = gate.reject()
    print(f"      during the command      refused={early}   (want True)")

    # Past the patience limit: the session must take new commands again.
    time.sleep(1.6)
    late = gate.reject()
    print(f"      after it wedged         refused={late}  (want False)")

    release.set()
    time.sleep(0.2)
    free = gate.reject()
    print(f"      once it finally returns refused={free}  (want False)")

    return early is True and late is False and free is False


def main_() -> None:
    print("  speech worker left cold, as it was when the hang happened\n")

    results: list[tuple[str, bool, float]] = []
    for n, command in enumerate(SEQUENCE, 1):
        print(f"  {n}. {command!r}")
        finished, took = run_like_voice_mode(command)
        print(f"      {took * 1000:.0f} ms" if finished
              else f"      HUNG - no answer in {LIMIT:.0f}s")
        results.append((command, finished, took))
        time.sleep(0.4)

    recovered = survives_a_stuck_command()

    print("\n" + "=" * 60)
    for command, ok, took in results:
        print(f"  {'PASS' if ok else 'HUNG'}  {took * 1000:7.0f} ms  {command}")
    print(f"  {'PASS' if recovered else 'FAIL'}            session survives a stuck command")
    print("=" * 60)

    hung = [c for c, ok, _ in results if not ok]
    if hung:
        print(f"  {len(hung)} command(s) never came back: {hung}")
        print("  the watchdog printed the exact stuck line above - bhej dena")
    if not recovered:
        print("  a wedged command still blocks the ones after it")
    if hung or not recovered:
        sys.exit(1)
    print("  no hang, and one stuck command no longer ends the session")


if __name__ == "__main__":
    main_()
