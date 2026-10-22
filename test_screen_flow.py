"""Test 3, end to end, through the real router and actions. No mic needed.

    set PYTHONIOENCODING=utf-8 && python test_screen_flow.py

This is your Test 3 - "search for Harsh Tiwari portfolio" / "click the
second link" / "go back" / "click the first link" - run the way voice mode
runs it, so what it exercises is the same code a spoken command hits:
router.route for the intent, actions.run for the doing, and state/screen
for knowing what is on screen already.

What it is actually checking is the part you wrote down: that LAYA reads
the page rather than clicking a remembered spot. Two things prove that and
both are printed below.

  The link it names. A fixed coordinate cannot tell you what it hit. This
  prints the text of the link it chose before it clicks, so a wrong choice
  is visible as a wrong choice instead of as a mystery page.

  The window title before and after. A click that changed nothing and a
  click that worked look identical otherwise, and the UIA path used to
  report both as "(unverified)". Now it compares.

Run it with Chrome already open and in front.

One thing this run taught, worth knowing before you read a failure wrong:
"the first link" means the first link in the window you are looking at. The
first time through, this script was launched from a terminal, the Claude
window took focus back mid-chain, and LAYA clicked the first link in THAT
window - correctly, by its own rule, and uselessly. So the script now pulls
the browser to the front before each click, which is the situation a spoken
command is actually in: you are looking at the page you are talking about.
If you see it click something from another app, focus moved - that is not
the router getting the intent wrong.
"""

from __future__ import annotations

import time

import actions
import router
import screen
import state

CHAIN = [
    "search for Harsh Tiwari portfolio",
    "click the second link",
    "go back",
    "click the first link",
]


def show_page() -> None:
    """What LAYA believes is on screen right now."""
    try:
        names = screen.links()
    except Exception as exc:
        print(f"      (could not read links: {type(exc).__name__}: {exc})")
        return
    print(f"      page has {len(names)} links; first three:")
    for n, name in enumerate(names[:3], 1):
        print(f"         {n}. {name[:58]}")


def main() -> None:
    print(f"  CDP live : {_cdp_live()}")
    print(f"  front    : {_front()}\n")

    for step, command in enumerate(CHAIN, 1):
        print(f"  {step}. {command!r}")
        if "click" in command or "back" in command:
            # Stand in for you looking at the browser. See the docstring.
            try:
                state.activate_process("chrome")
                time.sleep(1.0)
            except Exception:
                pass
        if "click" in command:
            show_page()

        before = _front()
        started = time.perf_counter()
        intent = router.route(command)
        result = actions.run(intent, raw_text=router.normalize(command))
        took = (time.perf_counter() - started) * 1000

        print(f"      -> {intent.name:<14} ok={result.ok}  {result.say[:58]}")
        if result.detail:
            for line in str(result.detail).splitlines():
                print(f"         {line.strip()}")
        print(f"      title: {before[:46]!r}")
        print(f"          -> {_front()[:46]!r}")
        print(f"      {took:.0f} ms\n")
        time.sleep(1.5)   # your cadence: one command, observe, next


def _front() -> str:
    try:
        window = screen.front_window()
        return (window.Name or "") if window is not None else "(none)"
    except Exception:
        return "(unreadable)"


def _cdp_live() -> bool:
    try:
        import browser

        return bool(browser.available())
    except Exception:
        return False


if __name__ == "__main__":
    main()
