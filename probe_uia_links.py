"""What does UIA actually hand back for a results page? Look before fixing.

"click the second link" picked Google's "click to view in Hindi" offer
instead of the second result. The obvious fix - match that phrase and drop
it - is the wrong one twice over: it is one language out of the many Google
offers this in, and the goal says these get solved in the command
architecture rather than patched per example.

So the question is whether the real results carry a signal the offer does
not. The CDP path answers this structurally (a result anchor wraps an h3),
and the candidate here is the url: screen._URL_TAIL exists because UIA
names arrive as "title site https://url", which means the raw name may say
where the link goes. A result goes somewhere; a page-level offer may not.

This prints the RAW names, before any trimming, so the answer is visible
rather than guessed at.

    set PYTHONIOENCODING=utf-8 && python probe_uia_links.py
"""

from __future__ import annotations

import screen

LABEL = {True: "has-url", False: "NO-URL "}


def main() -> None:
    window = screen.front_window()
    if window is None:
        print("  no front window")
        return
    print(f"  front: {window.Name}\n")

    auto = screen._uia()
    if auto is None:
        print("  uiautomation not available")
        return

    raw = screen._walk(window, screen._LINK_KINDS)
    print(f"  {len(raw)} raw hyperlinks before any filtering\n")

    for n, (depth, _, name) in enumerate(raw[:40], 1):
        has_url = bool(screen._URL_TAIL.search(name))
        shown = screen._URL_TAIL.sub("", name)[:62]
        print(f"  {n:3}. d={depth:<3} {LABEL[has_url]} [{len(shown):3}] {shown}")

    kept = screen._ordered(raw)
    print(f"\n  {len(kept)} kept by _ordered:\n")
    for n, (depth, _, name) in enumerate(kept, 1):
        print(f"  {n:3}. d={depth:<3} {name[:62]}")

    # The url check only means anything on the RAW names: _ordered strips
    # the tail with _URL_TAIL, so asking the kept list is asking after the
    # evidence was removed. Repetition is the signal that survives both.
    from collections import Counter
    counts = Counter(screen._clean(nm).lower() for _, _, nm in raw)
    worst = [(c, nm) for nm, c in counts.items() if c >= 3]
    print(f"\n  labels repeated 3+ times in the raw tree: {len(worst)}")
    for count, name in sorted(worst, reverse=True)[:5]:
        print(f"      {count}x  {name[:56]}")
    print("  those are furniture the page hangs on every row, not results.")


if __name__ == "__main__":
    main()
