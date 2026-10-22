"""What the STT changes actually cost and buy. Numbers, not opinions.

    set PYTHONIOENCODING=utf-8 && python measure_stt.py

Two settings changed: the decoder now sees the command vocabulary (derived
from the tables, config._command_words) and beam_size went from 1 to 5. The
second one costs time on every command you speak, so it needs a measured
price rather than an assurance.

READ THE ACCURACY COLUMN WITH CARE. The audio here is synthesised by the
Windows TTS voice, which is not your accent and is cleaner than any
microphone. So accuracy on this corpus mostly answers "did the change break
anything", not "will it stop writing 'see you' for 'save'" - that question
needs your voice, and nothing in this repo keeps a recording of it yet.

The latency column does transfer: decode time depends on the clip and the
beam width, not on who is speaking.
"""

from __future__ import annotations

import tempfile
import time
import wave
from pathlib import Path

import numpy as np

import config

# The commands that came back wrong in a live session, plus the ones most
# likely to regress if the prompt change went badly.
PHRASES = [
    "save",
    "close the window",
    "minimize karo",
    "copy karo",
    "close notepad",
    "notepad kholo",
    "chrome kholo",
    "click the second link",
    "search for Harsh Tiwari portfolio",
    "type hello this is laya",
    "go back",
    "new tab",
]

OLD_PROMPT = ("Hinglish laptop commands: food4all kholo, doc kholo, "
              "chrome kholo, notepad kholo, downloads folder dikhao, "
              "excel kholo, google karo.")


def synth(text: str, path: Path) -> bool:
    """Speak `text` into a 16 kHz mono wav. False if TTS is unavailable."""
    try:
        import pyttsx3

        engine = pyttsx3.init()
        engine.setProperty("rate", 170)
        engine.save_to_file(text, str(path))
        engine.runAndWait()
        engine.stop()
        return path.exists() and path.stat().st_size > 1000
    except Exception as exc:
        print(f"  (tts failed: {type(exc).__name__}: {exc})")
        return False


def load(path: Path) -> np.ndarray:
    """Read a wav as float32 mono at whisper's 16 kHz."""
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != 16000:
        # Linear resample is plenty - this is a decode-time measurement.
        want = int(len(audio) * 16000 / rate)
        audio = np.interp(np.linspace(0, len(audio) - 1, want),
                          np.arange(len(audio)), audio).astype(np.float32)
    return audio


def run(model, audio, **kwargs) -> tuple[str, float]:
    started = time.perf_counter()
    segments, _ = model.transcribe(audio, language=config.WHISPER_LANGUAGE,
                                   vad_filter=False,
                                   condition_on_previous_text=False, **kwargs)
    text = " ".join(s.text.strip() for s in segments).strip()
    return text, (time.perf_counter() - started) * 1000


def normalise(text: str) -> str:
    keep = "".join(c.lower() if c.isalnum() or c.isspace() else " " for c in text)
    return " ".join(keep.split())


def main() -> None:
    from faster_whisper import WhisperModel

    import listen

    device, compute = listen.pick_device()
    print(f"  model  : {config.WHISPER_MODEL} on {device}/{compute}")
    print(f"  hotwords: {len(config.WHISPER_HOTWORDS.split())} words\n")
    model = WhisperModel(config.WHISPER_MODEL, device=device,
                         compute_type=compute, download_root=str(config.CACHE))

    tmp = Path(tempfile.mkdtemp(prefix="laya_stt_"))
    clips: list[tuple[str, np.ndarray]] = []
    for n, phrase in enumerate(PHRASES):
        path = tmp / f"{n}.wav"
        if synth(phrase, path):
            clips.append((phrase, load(path)))
    if not clips:
        print("  no audio could be synthesised - nothing to measure")
        return
    print(f"  {len(clips)} clips synthesised\n")

    # Four settings, because the first run showed the two changes have very
    # different prices and it mattered which one was buying the accuracy:
    # the vocabulary is free, the beam width costs about a second a command.
    settings = {
        "OLD    beam=1, launch-only prompt": dict(
            initial_prompt=OLD_PROMPT, beam_size=1),
        "VOCAB  beam=1, derived vocabulary": dict(
            initial_prompt=config.WHISPER_PROMPT,
            hotwords=config.WHISPER_HOTWORDS, beam_size=1),
        "BOTH   beam=3, derived vocabulary": dict(
            initial_prompt=config.WHISPER_PROMPT,
            hotwords=config.WHISPER_HOTWORDS, beam_size=3),
        "BOTH   beam=5, derived vocabulary": dict(
            initial_prompt=config.WHISPER_PROMPT,
            hotwords=config.WHISPER_HOTWORDS, beam_size=5),
    }

    # Warm the model so the first row is not paying for lazy initialisation.
    run(model, clips[0][1], beam_size=1)

    totals: dict[str, tuple[int, float]] = {}
    for label, kwargs in settings.items():
        print(f"  {label}")
        right, spent = 0, 0.0
        for phrase, audio in clips:
            heard, took = run(model, audio, **kwargs)
            spent += took
            ok = normalise(heard) == normalise(phrase)
            right += ok
            flag = "  " if ok else "XX"
            print(f"    {flag} {took:6.0f}ms  {phrase!r}")
            if not ok:
                print(f"                  heard {heard!r}")
        totals[label] = (right, spent)
        print(f"    -> {right}/{len(clips)} exact, {spent / len(clips):.0f} ms/clip\n")

    print("  " + "-" * 60)
    for label, (right, spent) in totals.items():
        print(f"  {right:2}/{len(clips)}  {spent / len(clips):6.0f} ms/clip   {label}")
    base = list(totals.values())[0][1] / len(clips)
    print()
    for label, (_right, spent) in list(totals.items())[1:]:
        each = spent / len(clips)
        print(f"  {each - base:+6.0f} ms/command vs OLD   {label}")
    print("\n  accuracy here is a regression check, not a verdict - the voice "
          "is synthetic.\n  See the module docstring.")

    for path in tmp.glob("*.wav"):
        path.unlink(missing_ok=True)
    tmp.rmdir()


if __name__ == "__main__":
    main()
