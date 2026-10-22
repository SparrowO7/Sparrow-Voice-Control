"""Talk to a Chrome that Sparrow launched, over the DevTools protocol.

This is the precise half of "click on the second link". Instead of guessing
where the link is on screen, we ask the page: give me the result hrefs in
order. Then we navigate straight to the one you asked for - no click, so
nothing to miss, and an ad or a moved layout changes nothing.

The catch, and it is a real one: Chrome 136 stopped honouring
--remote-debugging-port on the default profile, for good security reasons.
So Sparrow has to run Chrome with its own --user-data-dir, which means a
separate profile: your logins, bookmarks and extensions are not in it. For
looking things up that is fine. For anything that needs you signed in, use
screen.py instead - that drives the Chrome you already have open.

Nothing here runs unless a click-by-position command asks for it.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.parse
from pathlib import Path

import config

PORT = 9222
_HOST = f"http://127.0.0.1:{PORT}"
_PROFILE = config.CACHE / "chrome-profile"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# What "the second link" means depends on the site, and getting it wrong is
# worse than failing: on a YouTube results page the generic collector below
# used to return seven links that were all the SAME video at different &t=
# offsets - the chapter markers inside the first result's card. "Click the
# second link" would have replayed the first video two minutes in and
# reported success, because something really was clicked.
#
# So the selectors are tried in order of how specifically they name a
# result, and the first family that finds anything wins. Each one was
# checked against the live page (probe_yt.py) rather than guessed.
#
# One collector, shared by "what are the links" and "click link N", so the
# number you are told is the number that gets clicked. It hands back the
# elements as well as the text, because clicking the anchor beats
# navigating to its href: Google wraps result hrefs in a /goto?url=...
# redirect that is not reliable to open on its own.
_COLLECT_JS = """
function __sparrowLinks() {
  const gather = (nodes, label) => {
    const seen = new Set();
    const out = [];
    for (const a of nodes) {
      if (!a || !a.href || seen.has(a.href)) continue;
      if (a.href.startsWith('javascript:')) continue;
      const t = (label(a) || '').trim();
      if (!t) continue;
      seen.add(a.href);
      out.push({el: a, title: t, href: a.href});
    }
    return out;
  };

  // YouTube search results. 'a#video-title' is the video itself; the
  // title attribute carries the full name even when the text is clipped.
  let out = gather(
    document.querySelectorAll('a#video-title'),
    a => a.title || a.innerText
  );
  if (out.length) return out;

  // Google. Result titles are an h3 inside the result anchor, which is
  // what skips the nav links, the "People also ask" box and the ad slots.
  out = gather(
    Array.from(document.querySelectorAll('a h3'))
         .map(h => h.closest('a')).filter(Boolean),
    a => { const h = a.querySelector('h3'); return h ? h.innerText : a.innerText; }
  );
  if (out.length) return out;

  // Anything else: visible links carrying enough text to be worth naming.
  return gather(
    document.querySelectorAll('a[href^="http"]'),
    a => a.innerText
  ).filter(x => x.title.length >= 8).slice(0, 25);
}
"""

_RESULT_JS = _COLLECT_JS + """
JSON.stringify(__sparrowLinks()
  .slice(0, 25)
  .map(x => ({title: x.title, href: x.href})))
"""

# Clicking the element itself, rather than opening its href, is what keeps
# this honest: whatever the page would do when you click that link is what
# happens, redirect wrapper and all.
_CLICK_NTH_JS = _COLLECT_JS + """
(() => {
  const links = __sparrowLinks();
  const want = %d;
  const index = want < 0 ? links.length + want : want - 1;
  const target = links[index];
  if (!target) return JSON.stringify({ok: false, count: links.length});
  target.el.scrollIntoView({block: 'center'});
  target.el.click();
  return JSON.stringify({ok: true, title: target.title, href: target.href});
})()
"""

# "click on the sign in button" - match on what the thing says, not where
# it sits. Exact text first, then a contains, so "sign in" does not pick
# "sign in with Google" when a plain "Sign in" is right there.
_CLICK_NAMED_JS = """
(() => {
  const want = %s;
  const nodes = Array.from(document.querySelectorAll(
    'a, button, input[type=submit], input[type=button], [role=button], [role=link]'
  ));
  const label = (e) => (
    (e.innerText || e.value || e.getAttribute('aria-label') || '')
  ).trim();
  const shown = (e) => {
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const usable = nodes.filter(shown);
  let hit = usable.find(e => label(e).toLowerCase() === want);
  if (!hit) hit = usable.find(e => label(e).toLowerCase().includes(want));
  if (!hit) return JSON.stringify({ok: false});
  hit.scrollIntoView({block: 'center'});
  hit.click();
  return JSON.stringify({ok: true, title: label(hit).slice(0, 80)});
})()
"""


def _get(path: str, timeout: float = 1.5):
    import requests

    try:
        reply = requests.get(_HOST + path, timeout=timeout)
        return reply.json() if reply.ok else None
    except Exception:
        return None


def is_live() -> bool:
    """Is there a debuggable Chrome listening right now?"""
    return _get("/json/version") is not None


def _page_targets() -> list[dict]:
    targets = _get("/json") or []
    return [
        t for t in targets
        if t.get("type") == "page"
        and t.get("webSocketDebuggerUrl")
        and not str(t.get("url", "")).startswith(("devtools://", "chrome://"))
    ]


def _connect(ws_url: str, timeout: float):
    """Open the DevTools socket.

    suppress_origin matters: websocket-client sends an Origin header, and
    Chrome 111+ answers one with 403 unless the browser was started with
    --remote-allow-origins. Sending no Origin at all is accepted, and it
    beats telling Chrome to accept every origin for the sake of this bot.
    """
    import websocket

    return websocket.create_connection(
        ws_url, timeout=timeout, suppress_origin=True
    )


def _raw_eval(target: dict, expression: str, timeout: float = 4.0):
    """Evaluate JS on one named target. Used to find the visible tab."""
    try:
        socket = _connect(target["webSocketDebuggerUrl"], timeout)
    except Exception:
        return None
    try:
        socket.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True},
        }))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                message = json.loads(socket.recv())
            except Exception:
                return None
            if message.get("id") == 1:
                result = message.get("result") or {}
                return (result.get("result") or {}).get("value")
        return None
    finally:
        try:
            socket.close()
        except Exception:
            pass


# The tab we are working in. /json lists targets in no useful order - the
# freshly opened about:blank came back ahead of the search results page -
# so once a tab is chosen it gets remembered for the follow-up commands.
# That is what makes "search for X" then "click the second link" land in
# the same place.
_pinned: str | None = None


def _page_target() -> dict | None:
    """The tab to act on: the pinned one, else the visible real page."""
    pages = _page_targets()
    if not pages:
        return None

    if _pinned:
        for target in pages:
            if target.get("id") == _pinned:
                return target

    real = [t for t in pages if str(t.get("url", "")).startswith("http")]
    if len(real) == 1:
        return real[0]
    if real:
        for target in real:
            if _raw_eval(target, "document.visibilityState") == "visible":
                return target
        return real[0]
    return pages[0]


def launch(url: str = "about:blank", wait: float = 20.0) -> bool:
    """Start Chrome with the debug port open, or reuse one already up.

    The wait is generous because the very first run has to build the
    profile directory from scratch, which took well over six seconds on
    this laptop. Afterwards the port answers almost instantly.
    """
    if is_live():
        return True

    from actions import resolve_app

    chrome = resolve_app("chrome")
    if chrome is None:
        return False

    _PROFILE.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen(
            [
                str(chrome),
                f"--remote-debugging-port={PORT}",
                f"--user-data-dir={_PROFILE}",
                "--no-first-run",
                "--no-default-browser-check",
                url,
            ],
            creationflags=_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False

    deadline = time.time() + wait
    while time.time() < deadline:
        if is_live():
            return True
        time.sleep(0.25)
    return False


def _send(method: str, params: dict | None = None, timeout: float = 8.0):
    """One CDP call against the active page. None on any failure.

    Responses and events share the socket, so the reply has to be matched
    on its id rather than simply read.
    """
    global _pinned

    target = _page_target()
    if target is None:
        return None

    try:
        socket = _connect(target["webSocketDebuggerUrl"], timeout)
    except Exception:
        return None

    # Whatever tab we just reached is the one the follow-up commands mean.
    if target.get("id"):
        _pinned = target["id"]

    try:
        socket.send(json.dumps({"id": 1, "method": method,
                                "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                message = json.loads(socket.recv())
            except Exception:
                return None
            if message.get("id") == 1:
                return message.get("result")
        return None
    finally:
        try:
            socket.close()
        except Exception:
            pass


def evaluate(expression: str):
    """Run JS in the page and hand back the value, or None."""
    result = _send("Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
        "awaitPromise": True,
    })
    if not result:
        return None
    if result.get("exceptionDetails"):
        return None
    return (result.get("result") or {}).get("value")


def navigate(url: str, settle: float = 2.5) -> bool:
    if _send("Page.navigate", {"url": url}) is None:
        return False
    time.sleep(settle)
    return True


def search(query: str) -> bool:
    """Run a Google search in the debuggable Chrome."""
    url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
    if not launch(url):
        return False
    # launch() may have reused a Chrome that is sitting somewhere else.
    return navigate(url)


def results() -> list[dict]:
    """[{title, href}] for the current page, best-effort and in page order."""
    raw = evaluate(_RESULT_JS)
    if not raw:
        return []
    try:
        found = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [r for r in found if isinstance(r, dict) and r.get("href")]


def unpin() -> None:
    """Forget which tab we were working in, so the next read re-detects it.

    Must be called by anything that changes tabs behind CDP's back. Pressing
    Ctrl+T opens a tab the protocol never hears about, and the pin then
    points at the page you just left: after "search for X" in a new tab,
    `results()` was still reading the YouTube page from the command before
    it, so "click the second link" would have clicked a link off the old
    page while you were looking at the new one.
    """
    global _pinned
    _pinned = None


def open_tab(url: str, settle: float = 3.0) -> bool:
    """Open `url` in a new tab through CDP, and pin it. Verified.

    Preferred over typing into the address bar whenever CDP is up, for two
    reasons that matter more than tidiness: the new tab's id comes back, so
    the pin follows the tab you are actually looking at, and the navigation
    can be read back instead of assumed.
    """
    global _pinned

    import requests

    try:
        reply = requests.put(
            _HOST + "/json/new?" + urllib.parse.quote(url, safe=":/?=&"),
            timeout=4.0,
        )
        if not reply.ok:
            return False
        target = reply.json()
    except Exception:
        return False

    if not isinstance(target, dict) or not target.get("id"):
        return False
    _pinned = target["id"]

    # Verify: poll until the tab reports the address we asked for. A tab
    # that opened but never loaded is not a successful search.
    want = url.split("#")[0]
    deadline = time.time() + settle
    while time.time() < deadline:
        here = url_of(_pinned)
        if here and (here.startswith(want[:60]) or want[:60] in here):
            return True
        time.sleep(0.25)
    return bool(url_of(_pinned))


def url_of(target_id: str) -> str:
    """The address of one named tab, read from the target list."""
    for target in _page_targets():
        if target.get("id") == target_id:
            return str(target.get("url", ""))
    return ""


def url() -> str:
    """The current page's address, or "" if the page cannot be reached."""
    return evaluate("location.href") or ""


def _tab_ids() -> set[str]:
    return {t["id"] for t in _page_targets() if t.get("id")}


def _landed(before_url: str, before_tabs: set[str], timeout: float) -> bool:
    """Did the click actually take us somewhere? Polled, never assumed.

    This is the verification step for clicking, and without it `ok: True`
    means only that the JS found an element and called .click() on it - not
    that anything happened. Those are genuinely different outcomes: a page
    can swallow the click in its own handler, and then "doosra link khol
    raha hoon" is spoken over a screen that never moved.

    Two ways a click can succeed, and both have to count:

      - the tab navigates, so location.href changes
      - the link is target=_blank, so this tab stays put and a NEW tab is
        the destination. Pinning it is what makes the next command ("go
        back", "click the first link") act on the page you are now looking
        at rather than the results page you left behind.
    """
    global _pinned

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.25)

        fresh = _tab_ids() - before_tabs
        if fresh:
            _pinned = next(iter(fresh))
            return True

        now = url()
        if now and now != before_url:
            return True
    return False


def click_nth(position: int, settle: float = 2.5) -> dict | None:
    """Click the nth link on the page. 1 = first, -1 = last.

    The return tells the caller what was clicked so it can say the title
    back - "Harsh Tiwari Portfolio khol raha hoon" is a far better answer
    than "clicked link 2", because it is checkable by ear.

    `landed` says whether the page actually went anywhere. The old code
    slept `settle` seconds and reported success on the strength of having
    called .click(), which is the same mistake the YouTube flow made.
    """
    if position == 0:
        return {"ok": False, "count": 0}

    before_url, before_tabs = url(), _tab_ids()

    raw = evaluate(_CLICK_NTH_JS % position)
    if not raw:
        return None
    try:
        outcome = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if outcome.get("ok"):
        outcome["landed"] = _landed(before_url, before_tabs, settle)
        outcome["url"] = url()
    return outcome


def click_named(name: str, settle: float = 2.5) -> dict | None:
    """Click the link or button whose text matches. None if we never got in.

    `landed` is reported but means less here than it does for click_nth. A
    named thing is often a button rather than a link - "click settings"
    opens a menu, "click play" starts a video - and neither changes the
    URL. So this one says whether the page moved; it does not decide that
    staying put was a failure. The caller makes that call.
    """
    if not name:
        return {"ok": False}

    before_url, before_tabs = url(), _tab_ids()

    raw = evaluate(_CLICK_NAMED_JS % json.dumps(name.strip().lower()))
    if not raw:
        return None
    try:
        outcome = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if outcome.get("ok"):
        outcome["landed"] = _landed(before_url, before_tabs, settle)
        outcome["url"] = url()
    return outcome
