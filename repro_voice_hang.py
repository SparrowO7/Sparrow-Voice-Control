"""Reproduce the hang with the keyboard hooks running. Nothing else differs.

The second voice command printed its intent line and stopped dead. Every
attempt to reproduce it so far has passed, and the reason is now obvious in
hindsight: those attempts ran commands in threads, with speech on, but with
NO keyboard listener installed. Voice mode always has one.

That matters more than it sounds. pynput's Windows listener installs a
low-level keyboard hook (WH_KEYBOARD_LL), and Windows calls that hook on
the thread that installed it - which must therefore keep pumping messages
and must return quickly. Meanwhile a command thread is inside EnumWindows
driving a Python callback per window, and SetForegroundWindow is doing
cross-process work. All of it needs the GIL, and the hook thread cannot
wait politely: Windows silently drops a hook that takes too long.

So this is the missing ingredient, and it is testable without a microphone
- the hooks install whether or not anyone speaks.

    python repro_voice_hang.py

It runs the exact sequence Harsh ran, in worker threads, with the real
listeners up, and the stack-dumping watchdog armed. If a command wedges,
the stack names the line.
"""

from __future__ import annotations

import sys
import threading
import time

import config

config.SPEAK_REPLIES = True        # the worker must be real; it was in his run

import actions   # noqa: E402
import main      # noqa: E402
import router    # noqa: E402
import speak     # noqa: E402

SEQUENCE = ["open notepad", "open chrome", "minimize karo", "restore window"]
LIMIT = 20.0


def start_listeners() -> list:
    """Install the same keyboard hooks voice mode installs.

    Both of them, because voice mode runs both when TRIGGER is "both" and
    the hooks are the variable under test. They get no callbacks here - we
    are not pressing the hotkey - but they are installed, pumping, and
    competing for the GIL, which is the whole point.
    """
    import listen

    listeners = []
    tap = listen.TapToTalk(lambda: None)
    threading.Thread(target=tap.run, daemon=True).start()
    listeners.append(tap)

    hold = listen.PushToTalk(lambda: None, lambda: None)
    threading.Thread(target=hold.run, daemon=True).start()
    listeners.append(hold)

    time.sleep(1.0)  # let the hooks actually install
    return listeners


def run_one(command: str) -> tuple[bool, float]:
    """One command in its own thread, watchdog armed, exactly like voice mode."""
    done = threading.Event()
    elapsed = [0.0]

    def work() -> None:
        started = time.perf_counter()
        guard = main._watchdog(command, seconds=4.0)
        try:
            intent = router.route(command)
            print(f"      intent  {intent.name}  term={intent.term!r}")
            result = actions.run(intent, raw_text=router.normalize(command))
            print(f"      -> ok={result.ok}  {result.say!r}")
        except Exception as exc:
            print(f"      -> EXC {type(exc).__name__}: {exc}")
        finally:
            guard.cancel()
            elapsed[0] = time.perf_counter() - started
            speak.say("theek hai")
            done.set()

    threading.Thread(target=work, daemon=True).start()
    return done.wait(LIMIT), elapsed[0]


def main_() -> None:
    print("  installing the real keyboard hooks (no mic needed)\n")
    try:
        start_listeners()
    except Exception as exc:
        print(f"  could not install listeners: {type(exc).__name__}: {exc}")
        print("  this test proves nothing without them - stopping")
        sys.exit(2)
    print("  hooks up. running the sequence that hung.\n")

    rows = []
    for n, command in enumerate(SEQUENCE, 1):
        print(f"  {n}. {command!r}")
        finished, took = run_one(command)
        print(f"      {took * 1000:.0f} ms\n" if finished
              else f"      HUNG - nothing in {LIMIT:.0f}s\n")
        rows.append((command, finished, took))
        time.sleep(0.5)

    print("=" * 60)
    for command, ok, took in rows:
        print(f"  {'PASS' if ok else 'HUNG'}  {took * 1000:7.0f} ms  {command}")
    print("=" * 60)

    hung = [c for c, ok, _ in rows if not ok]
    if hung:
        print(f"  REPRODUCED with hooks installed: {hung}")
        print("  the watchdog stack above names the stuck line")
        sys.exit(1)
    print("  not reproduced even with the hooks up")


if __name__ == "__main__":
    main_()
