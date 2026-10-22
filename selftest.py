"""Check the voice path without needing you to say anything.

Windows' own TTS speaks each command into a WAV, Whisper transcribes it, and
the router classifies the transcript. That exercises the whole chain except
the microphone itself:

    TTS -> wav -> faster-whisper -> router -> intent

A synthetic voice is cleaner than a real one, so treat a pass as "the wiring
is correct", not "it will understand you in a noisy room". Run it after
changing WHISPER_MODEL or WHISPER_LANGUAGE.

    python selftest.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

import config
import listen
import router

# Spoken out loud, then expected back. Mixed Hinglish and English on purpose.
PHRASES = [
    ("open chrome", "open_app"),
    ("notepad kholo", "open_app"),
    ("downloads kholo", "open_folder"),
    ("food4all ka doc kholo", "open_file"),
    ("mera food4all folder kholo", "open_folder"),
]


def say_to_wav(text: str, out: Path) -> bool:
    """Render text to a WAV with the Windows speech engine."""
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.SetOutputToWaveFile('{out}');"
        f"$s.Speak('{text}');"
        "$s.Dispose()"
    )
    done = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=60,
    )
    return done.returncode == 0 and out.is_file() and out.stat().st_size > 1000


def load_wav(path: Path) -> np.ndarray:
    """Read a WAV as mono float32 at config.SAMPLE_RATE."""
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())

    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)

    if rate != config.SAMPLE_RATE:
        # Linear resample is fine here - Whisper is not fussy about a
        # synthetic voice, and this keeps the dependency list short.
        target_n = int(len(audio) * config.SAMPLE_RATE / rate)
        audio = np.interp(
            np.linspace(0, len(audio) - 1, target_n),
            np.arange(len(audio)),
            audio,
        ).astype(np.float32)
    return audio


def check_microphone() -> bool:
    """Is there an input device at all? Says nothing about whether it works."""
    try:
        import sounddevice as sd

        inputs = [d for d in sd.query_devices() if d["max_input_channels"] > 0]
    except Exception as exc:
        print(f"  mic      FAIL  sounddevice unusable ({exc})")
        return False

    if not inputs:
        print("  mic      FAIL  no input device found")
        return False

    try:
        default = sd.query_devices(kind="input")["name"]
    except Exception:
        default = inputs[0]["name"]
    print(f"  mic      ok    {len(inputs)} input device(s), default: {default}")
    return True


def main() -> None:
    print("=" * 62)
    print("  voice path selftest")
    print("=" * 62)
    check_microphone()

    device, compute = listen.pick_device()
    print(f"  whisper  '{config.WHISPER_MODEL}' on {device}/{compute}, "
          f"language={config.WHISPER_LANGUAGE}")

    transcriber = listen.Transcriber()
    tmp = Path(tempfile.mkdtemp(prefix="sparrow-selftest-"))
    passed = 0

    for phrase, want_intent in PHRASES:
        wav = tmp / (phrase.replace(" ", "_") + ".wav")
        print(f'\n  said     "{phrase}"')

        if not say_to_wav(phrase, wav):
            print("  FAIL     could not render speech with the Windows TTS engine")
            continue

        heard = transcriber.transcribe(load_wav(wav))
        intent = router.route(heard)

        word_match = heard.lower().strip(" .,!?") == phrase.lower()
        print(f'  heard    "{heard}"{"" if word_match else "   <- not word-for-word"}')

        ok = intent.name == want_intent
        print(f"  intent   {'ok  ' if ok else 'MISS'}  {intent.name:12} "
              f"term=\"{intent.term}\"  want={want_intent}")
        passed += ok

    print("\n" + "=" * 62)
    print(f"  {passed}/{len(PHRASES)} commands survived the full voice chain")
    print("=" * 62)
    for leftover in tmp.glob("*.wav"):
        leftover.unlink(missing_ok=True)
    tmp.rmdir()
    sys.exit(0 if passed == len(PHRASES) else 1)


if __name__ == "__main__":
    main()
