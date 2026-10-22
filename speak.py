"""Spoken replies through Windows' built-in SAPI voice. No downloads.

pyttsx3 hangs if runAndWait() is called from different threads, so the
engine lives in one worker thread and everyone else just queues strings.
"""

from __future__ import annotations

import queue
import subprocess
import threading

import config

_queue: queue.Queue[str | None] = queue.Queue()
_worker: threading.Thread | None = None
_engine_ok = True


def _powershell_say(text: str) -> None:
    """Fallback that needs nothing installed at all."""
    safe = text.replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.Rate=1;$s.Speak('{safe}')"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _run() -> None:
    global _engine_ok
    engine = None
    try:
        import pyttsx3

        engine = pyttsx3.init()
        engine.setProperty("rate", config.TTS_RATE)
    except Exception:
        _engine_ok = False

    while True:
        text = _queue.get()
        if text is None:
            break
        if engine is not None and _engine_ok:
            try:
                engine.say(text)
                engine.runAndWait()
                continue
            except Exception:
                _engine_ok = False
        _powershell_say(text)


def warm() -> None:
    """Start the speech worker now, before any command runs.

    The worker's first act is `import pyttsx3`, which pulls in comtypes and
    executes a 410 KB generated SAPI wrapper, then calls pyttsx3.init() to
    stand up a COM object. That is the slowest moment in the program, and
    doing it at startup means the first reply is spoken as fast as the
    second rather than arriving a beat late.

    It was first written to cure the hang where the second voice command
    printed its intent and stopped dead - on the theory that this import
    held the interpreter's import lock while a command thread waited on its
    own lazy `import state`. prove_import_lock.py disproved that: per-module
    locks mean the two never contend, and the contended case measured 6ms.
    So this is a latency win, not the hang fix. The hang is still open and
    main.py's watchdog is what will pin it down.
    """
    global _worker
    if not config.SPEAK_REPLIES:
        return
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_run, daemon=True)
        _worker.start()


def say(text: str) -> None:
    """Queue a line. Returns immediately - speech happens in the background."""
    global _worker
    if not config.SPEAK_REPLIES or not text:
        return
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_run, daemon=True)
        _worker.start()
    _queue.put(text)


def stop() -> None:
    if _worker and _worker.is_alive():
        _queue.put(None)
