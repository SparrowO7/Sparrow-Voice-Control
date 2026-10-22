"""What this bot can actually do, proven case by case. Opens nothing."""
import time, router, actions

GROUPS = [
    ("APP KHOLNA", [
        "open chrome", "notepad kholo", "excel kholo", "vs code kholo",
        "calculator kholo", "spotify kholo",
    ]),
    ("FOLDER KHOLNA", [
        "downloads kholo", "desktop pe jao", "mera food4all folder kholo",
        "desktop pe wapas jao",
    ]),
    ("FILE DHUNDHNA", [
        "food4all ka doc kholo", "food4all ka pdf kholo",
        "i need that food4all writeup", "search for my resume",
        "ruffin food for all document.",
    ]),
    ("WEB SEARCH", [
        "google karo best laptop under 50000",
        "open chrome search harsh tiwari portfolio",
        "edge pe harsh tiwari portfolio dhundo",
    ]),
    ("YOUTUBE", [
        "youtube pe lofi search karo",
        "yt search for channel and play 1st video",
    ]),
    ("SLIDE / SCREEN CONTROL", [
        "next slide", "go back one slide", "jump to the first slide",
        "take me to the last slide", "move on to the next one",
        "presentation chalu karo", "black screen", "scroll down",
    ]),
    ("WINDOWS CONTROL", [
        "minimize karo", "desktop dikhao", "naya tab", "copy karo",
        "save karo", "screenshot lo", "full screen",
    ]),
    ("TYPE TEXT", ["likho hello world"]),
]

router._classify_with_laya("open chrome")  # warm, keep timings honest
for title, cases in GROUPS:
    print(f"\n=== {title} ===")
    for text in cases:
        t0 = time.perf_counter()
        it = router.route(text)
        ms = (time.perf_counter() - t0) * 1000
        try:
            pv = actions.preview(it, text)
            note = pv.say if pv.ok else f"X  {pv.say}"
        except Exception as exc:
            note = f"X  {type(exc).__name__}: {exc}"
        print(f'  {ms:>6.0f}ms  {it.name:15} {text!r}')
        print(f'            -> {note}')
