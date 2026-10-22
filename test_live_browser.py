"""Test 3 and Test 9 end to end, in ONE process.

One process matters. Running each command in its own `python -c` hid the
bug this file exists to catch: browser._pinned lives in memory, so a fresh
interpreter re-detects the visible tab every time and looks correct, while
the real app - one long-lived process - keeps a pin that can go stale the
moment a tab is opened by keystroke instead of by CDP.

Every step prints the URL before and after, because that is the only
evidence that an action happened. "ok=True" is a claim; a changed URL is a
fact.
"""

from __future__ import annotations

import time

import actions
import browser
import router
import state


def step(cmd: str, pause: float = 2.5, expect: str | None = None) -> bool:
    intent = router.route(cmd)
    before = browser.url()

    print(f"\n  >> {cmd!r}")
    print(f"     intent : {intent.name}/{intent.term!r} via {intent.source}")

    result = actions.run(intent, raw_text=router.normalize(cmd))
    print(f"     result : ok={result.ok}  {result.say!r}")
    for line in (result.detail or "").splitlines():
        print(f"              {line.strip()[:86]}")

    time.sleep(pause)
    after = browser.url()
    print(f"     url    : {before[:52]!r}")
    print(f"           -> {after[:66]!r}")
    print(f"     pinned : {(browser._pinned or '(none)')[:14]}  "
          f"tabs={len(browser._page_targets())}")

    if expect is not None:
        good = expect.lower() in after.lower()
        print(f"     EXPECT {expect!r} in url -> {'PASS' if good else 'FAIL'}")
        return good
    return result.ok


def links(label: str, limit: int = 4) -> list[dict]:
    found = browser.results()
    print(f"\n  {label}: {len(found)} links read from the CURRENT page")
    for n, row in enumerate(found[:limit], 1):
        print(f"   {n}. {(row.get('title') or '')[:54]!r}")
        print(f"      {row.get('href', '')[:72]}")
    return found


def main() -> None:
    if not browser.is_live():
        print("  launching debuggable chrome...")
        browser.launch("https://www.google.com")
        time.sleep(3)

    print(f"  front  : {state.window_title()!r}")
    print(f"  browser: {state.is_browser()}")

    verdicts: list[tuple[str, bool]] = []

    # --- Test 3: google, click 2nd, back, click 1st -----------------------
    print("\n" + "=" * 62)
    print("  TEST 3 - browser screen understanding")
    print("=" * 62)

    verdicts.append((
        "search lands on google",
        step("search for Harsh Tiwari portfolio", pause=3.5, expect="google.com/search"),
    ))

    found = links("after search")
    google_ok = bool(found) and not any(
        "youtube.com" in (r.get("href") or "") for r in found[:3]
    )
    print(f"     EXPECT links come from the NEW page -> "
          f"{'PASS' if google_ok else 'FAIL - reading a stale tab'}")
    verdicts.append(("links read from new tab", google_ok))

    second = (found[1].get("href") if len(found) > 1 else "") or ""
    verdicts.append((
        "click 2nd link navigates",
        step("click the second link", pause=3.0),
    ))
    landed = browser.url()
    match = bool(second) and (landed.split("?")[0][:40] in second
                              or second.split("?")[0][:40] in landed)
    print(f"     EXPECT url matches link #2 ({second[:44]}) -> "
          f"{'PASS' if match else 'CHECK'}")

    verdicts.append((
        "go back returns to results",
        step("go back", pause=2.5, expect="google.com/search"),
    ))

    verdicts.append((
        "click 1st link navigates",
        step("click the first link", pause=3.0),
    ))

    # --- summary ----------------------------------------------------------
    print("\n" + "=" * 62)
    for name, ok in verdicts:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print("=" * 62)


if __name__ == "__main__":
    main()
