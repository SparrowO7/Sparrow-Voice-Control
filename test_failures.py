"""Your failure list, measured instead of asked about. No mic needed.

    set PYTHONIOENCODING=utf-8 && python test_failures.py

You reported: "save kaam nahi krta notepad me fir close window kaam nahi
krta ... minimize kaam nhi krta window select kr pata current state ka pura
control sayad nhi hai".

Rather than hand that back to you to re-test, this runs each one through
the real router and actions and reads the WINDOW STATE afterwards to say
whether it actually happened. Every line below is a before/after fact, not
a return value - a return value is what said these worked in the first
place.
"""

from __future__ import annotations

import time

import actions
import router
import state

PAUSE = 1.2


def front() -> str:
    try:
        return f"{state.process_name()} | {state.window_title()[:44]}"
    except Exception:
        return "(unreadable)"


def notepad_handle() -> int:
    handle, _ = state.find_by_process("notepad")
    return handle


def step(command: str, note: str = "") -> object:
    print(f"\n  > {command!r}  {note}")
    before = front()
    intent = router.route(command)
    started = time.perf_counter()
    result = actions.run(intent, raw_text=router.normalize(command))
    took = (time.perf_counter() - started) * 1000
    print(f"      {intent.name:<14} ok={result.ok}  {result.say[:52]}")
    if result.detail:
        for line in str(result.detail).splitlines():
            print(f"        {line.strip()[:72]}")
    time.sleep(PAUSE)
    print(f"      front: {before[:52]}")
    print(f"          -> {front()[:52]}   ({took:.0f} ms)")
    return result


def main() -> None:
    print("=" * 66)
    print("  NOTEPAD: open / type / save / close")
    print("=" * 66)

    step("open notepad")
    handle = notepad_handle()
    print(f"      notepad window exists: {bool(handle)}")

    step("type hello this is LAYA")

    step("save", "<- you said this does nothing")
    handle_after = notepad_handle()
    print(f"      notepad still there: {bool(handle_after)}")
    print(f"      a dialog took over : {front().split('|')[0].strip()}")

    step("close window", "<- you said this does nothing")
    time.sleep(0.8)
    print(f"      notepad gone: {not notepad_handle()}")

    print("\n" + "=" * 66)
    print("  WINDOW CONTROL: minimize / restore")
    print("=" * 66)

    step("open notepad")
    handle = notepad_handle()
    step("minimize karo", "<- you said this does nothing")
    print(f"      actually minimised: {state.is_minimised(handle)}")
    step("restore window")
    print(f"      actually restored : {not state.is_minimised(handle)}")

    print("\n" + "=" * 66)
    print("  YOUTUBE: does it land on a search, or bounce to the home page?")
    print("=" * 66)
    for command in ["youtube pe lofi music search karo",
                    "yt pe harsh tiwari search karo"]:
        intent = router.route(command)
        preview = actions.preview(intent, raw_text=router.normalize(command))
        print(f"\n  > {command!r}")
        print(f"      {intent.name:<16} term={intent.term!r}")
        print(f"      would do: {preview.say[:60]}")
        if preview.detail:
            print(f"        {str(preview.detail).splitlines()[0][:70]}")


if __name__ == "__main__":
    main()
