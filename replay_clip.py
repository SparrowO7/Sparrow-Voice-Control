"""Re-run a clip LAYA already heard, under settings you choose.

    set PYTHONIOENCODING=utf-8 && python replay_clip.py            # list them
    set PYTHONIOENCODING=utf-8 && python replay_clip.py 3          # replay #3
    set PYTHONIOENCODING=utf-8 && python replay_clip.py all        # replay all

This is the other half of keeping clips. Saving the audio only helps if there
is a way to ask it questions, and the question is always the same one: was
the word unclear, or was the decoder primed badly?

The filename holds what Whisper wrote at the time, so a row where the replay
disagrees with the filename is a setting that changed the answer - which is
the measurement that was impossible before, because the audio was gone and
only the wrong text survived.

Clips come from config.CLIPS (cache/clips), newest last, capped at
config.KEEP_CLIPS.
"""

from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

import numpy as np

import config

OLD_PROMPT = ("Hinglish laptop commands: food4all kholo, doc kholo, "
              "chrome kholo, notepad kholo, downloads folder dikhao, "
              "excel kholo, google karo.")

SETTINGS = {
    "no prompt at all      ": dict(beam_size=1),
    "old launch-only prompt": dict(initial_prompt=OLD_PROMPT, beam_size=1),
    "current (vocab, greedy)": dict(initial_prompt=config.WHISPER_PROMPT,
                                    hotwords=config.WHISPER_HOTWORDS,
                                    beam_size=config.WHISPER_BEAM_SIZE),
    "vocab + beam=5        ": dict(initial_prompt=config.WHISPER_PROMPT,
                                   hotwords=config.WHISPER_HOTWORDS,
                                   beam_size=5),
}


def clips() -> list[Path]:
    if not config.CLIPS.exists():
        return []
    return sorted(config.CLIPS.glob("*.wav"), key=lambda p: p.stat().st_mtime)


def load(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def heard_from_name(path: Path) -> str:
    """What Whisper wrote when the clip was recorded, read off the filename."""
    stem = path.stem.split("_", 1)
    return stem[1].replace("_", " ") if len(stem) > 1 else "(unknown)"


def main() -> None:
    found = clips()
    if not found:
        print(f"  no clips in {config.CLIPS}")
        print("  speak a few commands with main.py running, then come back.")
        print(f"  (saving is on when config.KEEP_CLIPS > 0; it is "
              f"{config.KEEP_CLIPS})")
        return

    print(f"  {len(found)} clips in {config.CLIPS}\n")
    for n, path in enumerate(found, 1):
        seconds = path.stat().st_size / (config.SAMPLE_RATE * 2)
        print(f"  {n:3}. {seconds:4.1f}s  heard: {heard_from_name(path)!r}")

    which = sys.argv[1] if len(sys.argv) > 1 else ""
    if not which:
        print("\n  pass a number to replay one, or 'all'.")
        return

    chosen = found if which == "all" else [found[int(which) - 1]]

    from faster_whisper import WhisperModel

    import listen

    device, compute = listen.pick_device()
    print(f"\n  model: {config.WHISPER_MODEL} on {device}/{compute}")
    model = WhisperModel(config.WHISPER_MODEL, device=device,
                         compute_type=compute, download_root=str(config.CACHE))

    for path in chosen:
        audio = load(path)
        was = heard_from_name(path)
        print(f"\n  {path.name}")
        print(f"    when recorded: {was!r}")
        for label, kwargs in SETTINGS.items():
            started = time.perf_counter()
            segments, _ = model.transcribe(
                audio, language=config.WHISPER_LANGUAGE, vad_filter=False,
                condition_on_previous_text=False, **kwargs)
            text = " ".join(s.text.strip() for s in segments).strip()
            took = (time.perf_counter() - started) * 1000
            print(f"    {label}  {took:6.0f}ms  {text!r}")


if __name__ == "__main__":
    main()
