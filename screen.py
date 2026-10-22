"""Read and click what is on screen right now, in the window you are using.

Why this exists next to browser.py: browser.py drives a Chrome that Sparrow
launched, and Chrome 136+ refuses the debug port on your normal profile, so
that Chrome is a blank one - no logins, no bookmarks, no extensions. A blank
browser is the wrong answer to "search for X, now click the second link".

This file drives the window you already have open, signed in as you, by
reading the accessibility tree Windows exposes for it. Same mechanism a
screen reader uses, so it works in Chrome, Word, PowerPoint and Explorer
alike.

Two things to know about it:

  * Chrome keeps its page tree switched off until something asks. The first
    lookup is therefore slower than the rest, and may come back empty; ask
    again and the page is there. wake() does that for you.

  * "the second link" is not the second node in the tree. The tree hands
    back "Skip to main content" and the Images/Videos tabs first. The filter
    below is what turns tree order into the order you actually see.

Nothing here runs unless a screen command asks for it.
"""

from __future__ import annotations

import re
import time
from collections import Counter

# Walking deeper than this has never found a result and costs real time -
# Google's own content sits around depth 20.
_MAX_DEPTH = 40

# Hard ceiling per lookup. A tap-to-action budget over about a second feels
# broken, and this is on top of Whisper, so it stays tight.
_BUDGET = 6.0

# Scaffolding. Never what you meant, on any page, so always dropped.
_NEVER_NAMES = {
    "skip to main content", "accessibility help", "accessibility feedback",
    "accessibility links", "read more", "search by voice", "search by image",
    "google apps", "clear", "report inappropriate predictions",
    "go to google home",
}

# Google's own chrome around the results. Dropped only once we are sure we
# are looking at a results page - on a normal site "about" or "news" is a
# perfectly good thing to ask for by name.
_SEARCH_NAV_NAMES = {
    "ai mode", "all", "images", "videos", "shopping", "news", "forums",
    "books", "flights", "finance", "maps", "web", "more", "tools",
    "settings", "sign in", "feedback", "privacy", "terms", "about",
    "advertising", "business", "how search works", "next", "previous",
}

# A result's accessible name arrives as title + site + url run together:
#   "Harsh Tiwari | Web Developer harshtiwari.in https://harshtiwari.in"
# Cutting at the url leaves something worth saying out loud.
_URL_TAIL = re.compile(r"\s+https?://.*$|\s+www\.\S+.*$", re.IGNORECASE)


def _uia():
    """Import uiautomation lazily, so a missing package is not fatal."""
    try:
        import uiautomation

        uiautomation.uiautomation.DEBUG_SEARCH_TIME = False
        uiautomation.SetGlobalSearchTimeout(2)
        return uiautomation
    except Exception:
        return None


def available() -> bool:
    return _uia() is not None


def front_window():
    """The window you are looking at, or None."""
    auto = _uia()
    if auto is None:
        return None
    try:
        window = auto.GetForegroundControl()
        # The foreground control can be a child; climb to the real window.
        for _ in range(6):
            if window is None:
                return None
            if window.ControlTypeName == "WindowControl":
                return window
            window = window.GetParentControl()
        return None
    except Exception:
        return None


def find_window(fragment: str):
    """A top-level window whose title contains fragment. For testing."""
    auto = _uia()
    if auto is None or not fragment:
        return None
    needle = fragment.lower()
    try:
        for window in auto.GetRootControl().GetChildren():
            try:
                if window.Name and needle in window.Name.lower():
                    return window
            except Exception:
                continue
    except Exception:
        return None
    return None


def wake(window) -> bool:
    """Ask the window for its document, which is what turns Chrome's tree on.

    Chrome answers the first request by switching its renderer accessibility
    on and then populating, so this both triggers that and gives it a moment.
    """
    if window is None:
        return False
    try:
        document = window.DocumentControl(searchDepth=_MAX_DEPTH)
        if not document.Exists(1, 0.3):
            return False
        document.GetChildren()
        return True
    except Exception:
        return False


def _clean(name: str) -> str:
    """The sayable part of an accessible name."""
    return _URL_TAIL.sub("", (name or "").strip()).strip()


def _walk(window, kinds: tuple[str, ...]) -> list[tuple[int, object, str]]:
    """[(depth, control, name)] for the control types asked for, in page order.

    Stops at the time budget rather than running long, because a half list
    now is more use than a whole one after the moment has passed.
    """
    auto = _uia()
    if auto is None or window is None:
        return []

    found: list[tuple[int, object, str]] = []
    deadline = time.time() + _BUDGET
    try:
        for control, depth in auto.WalkControl(window, False, _MAX_DEPTH):
            if time.time() > deadline:
                break
            try:
                if control.ControlTypeName not in kinds:
                    continue
                name = (control.Name or "").strip()
            except Exception:
                continue
            if name:
                found.append((depth, control, name))
    except Exception:
        return found
    return found


def _ordered(entries: list[tuple[int, object, str]]):
    """Tree order -> the order you actually see on the page.

    The tree hands back nav links before content, so position in it means
    nothing. What does mean something: on a results page the real results
    all sit at one depth, there are several of them, and their names are
    long. Nav links are short and scattered across other depths.

    So pick the depth most of the long-named links share. That tunes itself
    per page, where hard-coding "depth 20" would die at Google's next
    layout change.
    """
    seen: set[str] = set()
    unique: list[tuple[int, object, str]] = []
    # Count every name BEFORE the dedup below throws the duplicates away.
    # That count is a signal and the dedup was destroying it - see _repeats.
    repeats = Counter(_clean(name).lower() for _, _, name in entries)
    for depth, control, name in entries:
        label = _clean(name)
        key = label.lower()
        if not label or key in seen or key in _NEVER_NAMES:
            continue
        seen.add(key)
        unique.append((depth, control, label))

    long_ones = [
        entry for entry in unique
        if len(entry[2]) >= 20 and entry[2].lower() not in _SEARCH_NAV_NAMES
    ]
    if len(long_ones) >= 3:
        depth, count = Counter(d for d, _, _ in long_ones).most_common(1)[0]
        if count >= 3:
            at_depth = [entry for entry in long_ones if entry[0] == depth]
            return _no_furniture(at_depth, repeats)

    return unique


def _no_furniture(entries: list, repeats: Counter) -> list:
    """Drop the one label the page prints next to every result.

    "click the second link" clicked Google's "view this in Hindi" offer,
    because that offer sits at the same tree depth as the results and is
    long enough to survive the length filter. Everything structural that
    distinguishes a result had already been tried and this one got through.

    The signal it does not survive is repetition. Google prints that offer
    once per result - eight times down the page, the identical string every
    time - while each actual result appears once. A label repeated at the
    results' own depth is furniture the page hangs on every row, and
    furniture is never what "the second link" means: an ordinal only makes
    sense for things that differ.

    Matching the phrase instead would have been shorter and wrong. Google
    shows it in whatever second language you read, so it would have fixed
    Hindi and nothing else, and it would have been the isolated patch this
    was explicitly not supposed to be. Repetition is none of those things:
    no language in it, no site, no fixed depth.

    Three, not two, because a page legitimately linking the same place
    twice is ordinary - two "Documentation" links in a header and a footer
    should not delete the entry. Eight is not that.
    """
    kept = [e for e in entries if repeats[e[2].lower()] < 3]
    # Only if something is left. A page where every row genuinely repeats
    # is better answered with the wrong ordering than with nothing at all.
    return kept or entries


def _target(window):
    """Resolve the window to act on, waking it if it is a browser."""
    window = window if window is not None else front_window()
    if window is not None:
        wake(window)
    return window


def _collect(window, kinds: tuple[str, ...]):
    """Walk once, and once more if the tree was still waking up."""
    entries = _ordered(_walk(window, kinds))
    if not entries:
        time.sleep(0.8)
        entries = _ordered(_walk(window, kinds))
    return entries


def _title() -> str:
    """The front window's title, or "" if it cannot be read."""
    try:
        window = front_window()
        return (window.Name or "") if window is not None else ""
    except Exception:
        return ""


def _landed(before: str, timeout: float = 2.5) -> bool | None:
    """Did the click actually go somewhere? Poll the title until it moves.

    The UIA path could not answer this before, and said so - every click
    came back "(unverified - UIA backend cannot read the url)". Requirement
    7 is that an important action gets checked rather than assumed, so a
    click that silently did nothing is exactly the case that must not pass
    as success.

    The url is genuinely out of reach here: UIA exposes the page, not the
    address bar's value in a form worth trusting. The title is not, and in
    a browser it changes on navigation - "harsh tiwari portfolio - Google
    Search - Google Chrome" became "हर्ष तिवारी का पोर्टफोलियो - Google Chrome",
    which is proof the click landed even though the url was never readable.

    Polling instead of sleeping is also why this is faster than what it
    replaces: a fixed 1.5s wait pays full price on every click, while this
    returns the moment the title moves and only spends the rest when it
    does not.

    Returns True if it moved, False if it never did, None if the title was
    unreadable at the start - in which case nothing was verified and the
    caller must not claim otherwise.
    """
    if not before:
        return None
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        time.sleep(0.15)
        now = _title()
        if now and now != before:
            return True
    return False


def _invoke(control) -> bool:
    """Activate the element. Invoke first, mouse only if it refuses.

    Invoke beats a click: nothing to aim at, no pointer yanked away from
    wherever you left it, and it works on an element scrolled out of view.
    """
    try:
        control.GetScrollItemPattern().ScrollIntoView()
    except Exception:
        pass

    try:
        pattern = control.GetInvokePattern()
        if pattern is not None:
            pattern.Invoke()
            return True
    except Exception:
        pass

    try:
        control.Click(simulateMove=False)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ public
_LINK_KINDS = ("HyperlinkControl",)
_PRESSABLE_KINDS = ("HyperlinkControl", "ButtonControl", "ListItemControl",
                    "MenuItemControl", "TabItemControl", "CheckBoxControl",
                    "RadioButtonControl")


def links(window=None) -> list[str]:
    """What "the first link", "the second link" and so on refer to."""
    return [name for _, _, name in _collect(_target(window), _LINK_KINDS)]


def buttons(window=None) -> list[str]:
    """Named things that can be pressed in the front window."""
    entries = _collect(_target(window), ("ButtonControl",))
    return [name for _, _, name in entries]


def click_nth(position: int, window=None) -> dict:
    """Click the nth link in the window you are using. 1 = first, -1 = last."""
    if position == 0:
        return {"ok": False, "count": 0}

    found = _target(window)
    if found is None:
        return {"ok": False, "count": 0, "reason": "no window"}

    entries = _collect(found, _LINK_KINDS)
    if not entries:
        return {"ok": False, "count": 0}

    index = len(entries) + position if position < 0 else position - 1
    if not 0 <= index < len(entries):
        return {"ok": False, "count": len(entries)}

    _, control, name = entries[index]
    before = _title()
    if not _invoke(control):
        return {"ok": False, "count": len(entries), "title": name}
    return {"ok": True, "title": name, "count": len(entries),
            "landed": _landed(before), "was": before}


def click_named(name: str, window=None) -> dict:
    """Click the link or button whose text matches what you said.

    Exact match first, then a contains, so asking for "sign in" does not
    land on "sign in with Google" when a plain "Sign in" is right there.
    """
    if not name:
        return {"ok": False}

    found = _target(window)
    if found is None:
        return {"ok": False, "reason": "no window"}

    wanted = name.strip().lower()
    entries = _collect(found, _PRESSABLE_KINDS)

    hit = next((e for e in entries if e[2].lower() == wanted), None)
    if hit is None:
        hit = next((e for e in entries if wanted in e[2].lower()), None)
    if hit is None:
        return {"ok": False, "count": len(entries)}

    before = _title()
    if not _invoke(hit[1]):
        return {"ok": False, "title": hit[2], "count": len(entries)}
    return {"ok": True, "title": hit[2], "count": len(entries),
            # Shorter than click_nth's wait on purpose. "click the second
            # link" is expected to navigate, so it is worth waiting out;
            # "click settings" usually opens a menu and never changes the
            # title, and waiting 2.5s to confirm a non-event on every such
            # click is latency spent for nothing.
            "landed": _landed(before, timeout=1.2), "was": before}
