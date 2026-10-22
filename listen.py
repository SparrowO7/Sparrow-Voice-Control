"""Mic capture (hold-to-talk) and speech-to-text via faster-whisper."""

from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np
import sounddevice as sd

import config


# ------------------------------------------------------------------ device
def pick_device() -> tuple[str, str]:
    """Return (device, compute_type) for faster-whisper.

    With the MUX switch off the 4050 is invisible, so this lands on CPU +
    int8 and sips battery. Flip the dGPU on and it upgrades by itself.
    """
    wanted = config.DEVICE
    if wanted == "cpu":
        return "cpu", "int8"
    if wanted == "cuda":
        return "cuda", "float16"

    try:
        import torch

        if torch.cuda.is_available():
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def resolve_mic():
    """config.MIC_DEVICE as a sounddevice index, or None for the default."""
    wanted = config.MIC_DEVICE
    if wanted in (None, ""):
        return None
    if isinstance(wanted, int) or str(wanted).isdigit():
        return int(wanted)

    needle = str(wanted).lower()
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0 and needle in device["name"].lower():
            return index
    print(f"[mic] no input device matching '{wanted}', using the default")
    return None


# ------------------------------------------------------------------ mic
class Microphone:
    def __init__(self) -> None:
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()

    def _callback(self, indata, frames, time_info, status) -> None:
        with self._lock:
            self._frames.append(indata.copy())

    def start(self) -> None:
        if self._stream is not None:
            return
        with self._lock:
            self._frames = []
        self._stream = sd.InputStream(
            samplerate=config.SAMPLE_RATE,
            channels=1,
            dtype="float32",
            device=resolve_mic(),
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        if self._stream is None:
            return np.zeros(0, dtype=np.float32)
        self._stream.stop()
        self._stream.close()
        self._stream = None
        with self._lock:
            frames = self._frames
            self._frames = []
        if not frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(frames, axis=0).flatten().astype(np.float32)

    def level(self, window: int = 6) -> float:
        """Loudness of the last few callbacks. 0.0 if nothing has arrived."""
        with self._lock:
            recent = self._frames[-window:]
        if not recent:
            return 0.0
        return float(np.sqrt(np.mean(np.square(np.concatenate(recent)))))

    def record_until_silence(self) -> np.ndarray:
        """Record from a single tap, and stop when you stop talking.

        An earbud button sends a tap, not a hold, so there is no release
        event to end the clip. Loudness ends it instead: after a lead-in
        grace period (you need a moment to start speaking), a short stretch
        of quiet means you finished.
        """
        self.start()
        started = time.monotonic()
        quiet_since: float | None = None

        while True:
            time.sleep(0.05)
            now = time.monotonic()
            elapsed = now - started
            if elapsed >= config.TAP_MAX_SECONDS:
                break

            if self.level() >= config.SILENCE_RMS:
                quiet_since = None          # still talking
            elif elapsed >= config.TAP_LEAD_IN:
                quiet_since = quiet_since or now
                if now - quiet_since >= config.TAP_SILENCE_TAIL:
                    break

        return self.stop()


# ------------------------------------------------------------------ stt
class Transcriber:
    def __init__(self) -> None:
        self._model = None
        self.last_skip = ""

    def load(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel

        device, compute_type = pick_device()
        print(f"[stt] loading whisper '{config.WHISPER_MODEL}' on {device}/{compute_type}")
        self._model = WhisperModel(
            config.WHISPER_MODEL,
            device=device,
            compute_type=compute_type,
            download_root=str(config.CACHE),
        )

    def transcribe(self, audio: np.ndarray) -> str:
        # Why the last empty answer was empty. The caller speaks this back:
        # a command that produces no text produced no log line either, so
        # "save" and "close window" looked like broken commands when what
        # actually happened is that they never became words at all.
        self.last_skip = ""

        if audio.size < config.SAMPLE_RATE * 0.3:
            self.last_skip = "clip too short - held the key for under a third of a second"
            return ""  # a tap, not a sentence

        # A silent clip makes Whisper parrot initial_prompt straight back,
        # and "Hinglish laptop commands" then routes as a real command.
        # Cheaper and safer to never ask it.
        level = float(np.sqrt(np.mean(np.square(audio))))
        if level < config.SILENCE_RMS:
            print(f"\n  (silence - mic level {level:.4f}. Wrong input device?)")
            self.last_skip = (f"too quiet - mic level {level:.4f}, "
                              f"needs {config.SILENCE_RMS}")
            return ""

        self.load()
        segments, _ = self._model.transcribe(
            audio,
            language=config.WHISPER_LANGUAGE,
            initial_prompt=config.WHISPER_PROMPT,
            # The command vocabulary, derived from the tables - see
            # config._command_words. It occupies its own slot in the decoder
            # prompt alongside initial_prompt rather than replacing it;
            # faster_whisper.get_prompt emits hotwords and then the prompt
            # tokens, so both are in context.
            hotwords=config.WHISPER_HOTWORDS,
            # Greedy, and that is the measured answer rather than the assumed
            # one. I raised this to 5 on the reasoning that a one-word clip
            # has no later context to correct an early wrong guess - which
            # sounds right and is not what happened: the wider beam changed
            # no transcript and cost ~400 ms a command. The accuracy came
            # entirely from the vocabulary above. See config.WHISPER_BEAM_SIZE
            # for the table and measure_stt.py to re-run it.
            beam_size=config.WHISPER_BEAM_SIZE,
            vad_filter=False,     # push-to-talk already gives clean boundaries
            condition_on_previous_text=False,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        if not text:
            self.last_skip = (f"whisper heard nothing in {audio.size / config.SAMPLE_RATE:.1f}s "
                              f"at level {level:.4f}")
        _keep_clip(audio, text)
        return text


def _keep_clip(audio: np.ndarray, heard: str) -> None:
    """Write this clip to cache/clips, keeping only the newest KEEP_CLIPS.

    Never raises and never blocks the command: a failure here costs a
    diagnostic, and the command the user is waiting on costs more.

    The filename carries what was heard, so a wrong transcript is findable
    without opening anything - "0007_see_you.wav" next to a command you know
    you said as "save" is the whole bug report.
    """
    if config.KEEP_CLIPS <= 0:
        return
    try:
        import wave

        config.CLIPS.mkdir(parents=True, exist_ok=True)
        label = "".join(c if c.isalnum() else "_" for c in (heard or "nothing"))
        name = f"{time.strftime('%H%M%S')}_{label[:40] or 'nothing'}.wav"
        with wave.open(str(config.CLIPS / name), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(config.SAMPLE_RATE)
            clipped = np.clip(audio, -1.0, 1.0)
            handle.writeframes((clipped * 32767).astype(np.int16).tobytes())

        old = sorted(config.CLIPS.glob("*.wav"), key=lambda p: p.stat().st_mtime)
        for path in old[:-config.KEEP_CLIPS]:
            path.unlink(missing_ok=True)
    except Exception:
        pass


# ------------------------------------------------------------------ hotkey
_KEY_NAMES = {
    "ctrl": {"ctrl", "ctrl_l", "ctrl_r"},
    "alt": {"alt", "alt_l", "alt_r", "alt_gr"},
    "shift": {"shift", "shift_l", "shift_r"},
}


def _normalise(key) -> str | None:
    name = getattr(key, "name", None) or getattr(key, "char", None)
    if not name:
        return None
    for canonical, variants in _KEY_NAMES.items():
        if name in variants:
            return canonical
    return name


class PushToTalk:
    """Calls on_press once all hotkey keys are down, on_release when one lifts."""

    def __init__(self, on_start: Callable[[], None], on_stop: Callable[[], None]) -> None:
        self.on_start = on_start
        self.on_stop = on_stop
        self._down: set[str] = set()
        self._active = False
        self._triggered = False  # prevent Windows key-repeat re-triggers

    def _press(self, key) -> None:
        name = _normalise(key)
        if name:
            self._down.add(name)
        if not self._triggered and config.HOTKEY <= self._down:
            self._triggered = True
            self._active = True
            self.on_start()

    def _release(self, key) -> None:
        name = _normalise(key)
        if name:
            self._down.discard(name)
        if self._active and not config.HOTKEY <= self._down:
            self._active = False
            self._triggered = False
            self.on_stop()

    def run(self) -> None:
        from pynput import keyboard

        with keyboard.Listener(on_press=self._press, on_release=self._release) as listener:
            listener.join()


class TapToTalk:
    """One tap on your earbuds starts a command. Nothing to hold.

    This is the whole point of the earbud trigger: during a talk you are
    nowhere near the laptop keyboard, so a hold-to-talk hotkey is useless.
    """

    def __init__(self, on_tap: Callable[[], None]) -> None:
        self.on_tap = on_tap
        self._listener = None

    def _press(self, key) -> None:
        if _normalise(key) != config.TAP_KEY:
            return

        # Stop the tap from also pausing Spotify. pynput can only do this
        # on some setups; if it can't, the tap does double duty and that is
        # a nuisance rather than a failure.
        if config.TAP_SUPPRESS and self._listener is not None:
            try:
                self._listener.suppress_event()
            except Exception:
                pass

        self.on_tap()

    def run(self) -> None:
        from pynput import keyboard

        self._listener = keyboard.Listener(on_press=self._press)
        with self._listener as listener:
            listener.join()
