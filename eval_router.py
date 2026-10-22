"""Does LAYA actually beat the keyword table? Measure, don't guess.

    python eval_router.py            # all three routers
    python eval_router.py keywords   # skip the slow one

Prints per-case verdicts and a summary of accuracy + latency, so you can
decide whether LAYA is worth its ~1.2s on this machine.
"""

from __future__ import annotations

import os
import sys
import time

import config
import router

# (text, expected_intent, expected_file_type or None to ignore)
CASES = [
    # --- phrasings the keyword table was written for -------------------
    ("open chrome",                          "open_app",    None),
    ("notepad kholo",                        "open_app",    None),
    ("excel kholo",                          "open_app",    None),
    ("downloads kholo",                      "open_folder", None),
    ("food4all ka doc kholo",                "open_file",   "doc"),
    ("food4all ka pdf kholo",                "open_file",   "pdf"),
    ("google karo best laptop under 50000",  "web_search",  None),
    ("mera food4all folder kholo",           "open_folder", None),

    # --- phrasings nobody listed --------------------------------------
    ("i need that food4all writeup",         "open_file",   "doc"),
    ("pull up my resume",                    "open_file",   None),
    ("fire up the browser",                  "open_app",    None),
    ("show me where my downloads are",       "open_folder", None),
    ("mujhe food4all ki architecture chahiye", "open_file", None),
    ("wo sheet kholo jisme expenses hain",   "open_file",   "sheet"),
    ("youtube pe lofi search karo",          "web_search",  None),
    ("desktop pe jao",                       "open_folder", None),
    ("zara us presentation ko nikalo",       "open_file",   "slides"),
    ("what is the capital of france",        "web_search",  None),

    # --- presentation control, listed phrasings -----------------------
    ("next slide",                           "press_keys",  None),
    ("agla slide",                           "press_keys",  None),
    ("sparrow next slide please",             "press_keys",  None),
    ("pichla slide",                         "press_keys",  None),
    ("presentation chalu karo",              "press_keys",  None),
    ("black screen",                         "press_keys",  None),

    # --- presentation control, phrasings nobody listed ----------------
    ("move on to the next one",              "press_keys",  None),
    ("go back one slide",                    "press_keys",  None),
    ("take me to the last slide",            "press_keys",  None),

    # --- the trap: contains "wapas", must still be a folder ----------
    ("desktop pe wapas jao",                 "open_folder", None),

    # --- naming a browser plus a search: the web, not a bare launch ---
    # This one shipped broken: it launched Chrome and threw away
    # "harsh tiwari portfolio" without a word.
    ("open chrome search harsh tiwari portfolio", "web_search", None),
    ("chrome pe harsh tiwari portfolio search karo", "web_search", None),
    ("chrome me search karo harsh tiwari portfolio", "web_search", None),
    ("edge pe harsh tiwari portfolio dhundo", "web_search",  None),
    ("search harsh tiwari portfolio",         "web_search",  None),
    # ...but a browser on its own is still just a launch.
    ("chrome kholo",                          "open_app",    None),
    # ...and a search verb pointing at this laptop is still a file.
    ("search for my resume",                  "open_file",   None),
]


def run_keywords(text: str) -> tuple[str, str, float]:
    started = time.perf_counter()
    text = router.normalize(text)
    spec = router._match_key_command(text)
    if spec:
        return "press_keys", "any", (time.perf_counter() - started) * 1000
    term = router.extract_term(text)
    intent, file_type = router._classify_with_keywords(text, term)
    return intent, file_type, (time.perf_counter() - started) * 1000


def run_laya(text: str) -> tuple[str, str, float]:
    started = time.perf_counter()
    verdict = router._classify_with_laya(text)
    took = (time.perf_counter() - started) * 1000
    if not verdict:
        return "unknown", "any", took
    intent, file_type, _, _ = verdict
    return intent, file_type, took


def run_hybrid(text: str) -> tuple[str, str, float]:
    started = time.perf_counter()
    intent = router.route(text)
    return intent.name, intent.file_type, (time.perf_counter() - started) * 1000


RUNNERS = {"keywords": run_keywords, "laya": run_laya, "hybrid": run_hybrid}


def main() -> None:
    wanted = sys.argv[1:] or list(RUNNERS)
    scores = {name: {"intent": 0, "type": 0, "type_n": 0, "ms": 0.0} for name in wanted}

    # Load LAYA's weights before timing anything, otherwise the ~25s cold
    # start lands on one unlucky case and poisons the average.
    if any(name in ("laya", "hybrid") for name in wanted):
        os.environ.pop("SPARROW_NO_LAYA", None)
        print("warming LAYA (one-time weight load)...")
        started = time.perf_counter()
        router._classify_with_laya("open chrome")
        print(f"ready in {time.perf_counter() - started:.1f}s")

    for text, want_intent, want_type in CASES:
        print(f'\n"{text}"')
        print(f"  want            {want_intent:12} {want_type or '-'}")
        for name in wanted:
            if name == "keywords":
                os.environ["SPARROW_NO_LAYA"] = "1"
            elif name == "laya":
                os.environ.pop("SPARROW_NO_LAYA", None)
                os.environ["SPARROW_LAYA_FIRST"] = "1"
            else:
                os.environ.pop("SPARROW_NO_LAYA", None)
                os.environ.pop("SPARROW_LAYA_FIRST", None)

            got_intent, got_type, ms = RUNNERS[name](text)
            tick = "ok  " if got_intent == want_intent else "MISS"
            scores[name]["ms"] += ms
            scores[name]["intent"] += got_intent == want_intent

            type_note = ""
            if want_type:
                scores[name]["type_n"] += 1
                hit = got_type == want_type
                scores[name]["type"] += hit
                type_note = f"  type={got_type}{'' if hit else ' <- wrong'}"
            print(f"  {name:8} {tick}  {got_intent:12} {ms:>7.0f}ms{type_note}")

    total = len(CASES)
    print("\n" + "=" * 62)
    print(f"{'router':10} {'intent':>12} {'file_type':>12} {'avg latency':>14}")
    print("-" * 62)
    for name in wanted:
        s = scores[name]
        type_str = f"{s['type']}/{s['type_n']}" if s["type_n"] else "-"
        print(f"{name:10} {s['intent']:>7}/{total:<4} {type_str:>12} "
              f"{s['ms'] / total:>11.0f}ms")
    print("=" * 62)


if __name__ == "__main__":
    main()
