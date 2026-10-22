"""Which selector actually names a YouTube search RESULT?

Written because the generic link collector in browser.py reads a YouTube
results page wrong, and wrong in a way that looks plausible: it returned
seven "links" that were all the same video at different &t= offsets. Those
are the chapter markers inside the first result's card, so "click the
second link" would have replayed the first video 2 minutes in rather than
opening the second video.

Google's results have a stable shape (a > h3) and YouTube's do not - the
layout has been rewritten twice (ytd-video-renderer, then
yt-lockup-view-model), so the only honest way to pick a selector is to ask
the page that is actually open.
"""

from __future__ import annotations

import json

import browser

PROBE = r"""
JSON.stringify((() => {
  const cands = [
    "a#video-title",
    "ytd-video-renderer a#video-title",
    "a.yt-lockup-metadata-view-model__title",
    "yt-lockup-view-model a[href*='/watch']",
    "a[href*='/watch']",
    "ytd-video-renderer",
    "yt-lockup-view-model",
    "a[href*='/watch']:not([href*='&t='])"
  ];
  const out = [];
  for (const sel of cands) {
    try {
      const n = document.querySelectorAll(sel);
      const first = n[0] ? (n[0].innerText || n[0].title || "").trim() : null;
      out.push({sel: sel, count: n.length, first: first ? first.slice(0, 50) : null});
    } catch (e) {
      out.push({sel: sel, err: String(e).slice(0, 50)});
    }
  }
  return out;
})())
"""

# What a correct collector must produce: distinct videos, in page order.
DISTINCT = r"""
JSON.stringify((() => {
  const seen = new Set();
  const out = [];
  for (const a of document.querySelectorAll("a[href*='/watch']")) {
    const m = (a.href || "").match(/[?&]v=([\w-]{6,})/);
    if (!m || seen.has(m[1])) continue;
    const label = (a.innerText || a.title || "").trim();
    if (label.length < 4) continue;
    seen.add(m[1]);
    out.push({v: m[1], title: label.slice(0, 55)});
    if (out.length >= 8) break;
  }
  return out;
})())
"""


def main() -> None:
    if not browser.is_live():
        print("  no debuggable chrome - run browser.launch() first")
        return

    print(f"  url: {browser.url()}\n")

    raw = browser.evaluate(PROBE)
    if not raw:
        print("  probe returned nothing")
        return
    for row in json.loads(raw):
        if "err" in row:
            print(f"  {row['sel']:46} ERROR {row['err']}")
        else:
            print(f"  {row['sel']:46} n={row['count']:<4} {row['first']!r}")

    print("\n  --- distinct videos, deduped by ?v= ---")
    raw = browser.evaluate(DISTINCT)
    if raw:
        for n, row in enumerate(json.loads(raw), 1):
            print(f"   {n}. [{row['v']}] {row['title']!r}")


if __name__ == "__main__":
    main()
