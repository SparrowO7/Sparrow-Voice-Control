"""One-time model download + a peek at LAYA's actual response shape.

Run this once after install, on wifi:

    python warmup.py

It pulls the LAYA checkpoint (~650 MB) and the Whisper weights (~480 MB)
into cache/ so the first real command isn't a five-minute wait.
"""

from __future__ import annotations

import json
import sys
import time

import config


def warm_laya() -> None:
    print("\n[1/2] LAYA")
    started = time.perf_counter()
    try:
        from laya import Router
    except ImportError:
        print("      laya not installed - skipping (pip install laya)")
        return

    router = Router()
    print(f"      loaded in {time.perf_counter() - started:.1f}s")

    import router as sparrow_router

    samples = [
        "open chrome",
        "food4all ka doc kholo",
        "downloads folder kholo",
        "google karo best laptop under 50000",
    ]
    for text in samples:
        started = time.perf_counter()
        result = router.predict(text, sparrow_router.QUESTIONS)
        took = (time.perf_counter() - started) * 1000
        print(f'\n      "{text}"  ({took:.0f}ms)')
        print("      " + json.dumps(result, indent=2, default=str).replace("\n", "\n      "))


def warm_whisper() -> None:
    print("\n[2/2] Whisper")
    started = time.perf_counter()
    try:
        import listen
    except ImportError as exc:
        print(f"      skipping ({exc})")
        return

    stt = listen.Transcriber()
    stt.load()
    print(f"      loaded in {time.perf_counter() - started:.1f}s")


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    config.CACHE.mkdir(parents=True, exist_ok=True)
    if only in ("", "laya"):
        warm_laya()
    if only in ("", "whisper"):
        warm_whisper()
    print("\nDone. Models are cached - startup is fast from now on.")
