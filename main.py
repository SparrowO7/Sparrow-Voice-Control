"""Sparrow: hold Ctrl+Alt, say what to open, let go.

    python main.py                          # voice mode
    python main.py --text                   # type instead of talk (no mic/whisper)
    python main.py "food4all ka doc kholo"  # one shot
    python main.py --text --dry             # show the decision, open nothing
    python main.py --reindex                # rebuild the fallback file index
"""

from __future__ import annotations

import sys
import threading
import time
import traceback
from pathlib import Path

import actions
import config
import router
import search
import speak

DRY_RUN = False
LOG_FILE = config.CACHE / "sparrow.log"
PENDING_CONFIRMATION = None


def log(line: str) -> None:
    """Append to cache/sparrow.log.

    The console window scrolls away and gets closed. When a command
    misfires, this is the only record of what Whisper actually heard -
    which is almost always where the problem is.
    """
    try:
        config.CACHE.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {line}\n")
    except OSError:
        pass


def _watchdog(label: str, seconds: float = 4.0) -> threading.Timer:
    """If a command is still running after `seconds`, say WHERE it is stuck.

    A command that hangs used to leave the console sitting on its "intent"
    line with nothing to go on - no way to tell a slow launch from a wedged
    one, which is the same failure as a clip that produced no transcript:
    the bot is doing something, or nothing, and both look identical.

    Printing "still running" was the first attempt and it was not enough. It
    tells you the command hung, which you could already see. So this prints
    the stack of the thread that is stuck, which names the exact line - and
    that is the difference between diagnosing this and guessing at it again.
    Two wrong guesses have already been paid for: an import-lock theory that
    measurement disproved (prove_import_lock.py) and a comtypes theory the
    file timestamps disproved.

    The frames go to the log as well as the screen, because the console
    scrolls and this is the one moment worth keeping.
    """
    me = threading.current_thread().ident

    def bark() -> None:
        lines = [f"  slow    still running after {seconds:.0f}s: {label}"]
        frame = sys._current_frames().get(me)
        if frame is None:
            lines.append("          (thread already gone - not a hang)")
        else:
            # Innermost frames last, same order as a traceback, trimmed to
            # our own files: the stdlib frames above are never the answer.
            #
            # Matched against the project directory rather than a folder
            # NAME. It used to look for the literal string "jarvis" in the
            # path, which meant renaming the folder silently emptied this
            # list and the watchdog stopped naming the line it was stuck on -
            # the one thing it exists to do.
            stack = traceback.extract_stack(frame)
            home = str(config.PROJECT).lower()
            ours = [f for f in stack
                    if home in (f.filename or "").lower()]
            for entry in (ours or stack)[-6:]:
                where = Path(entry.filename).name
                lines.append(f"          {where}:{entry.lineno} in {entry.name}()")
                if entry.line:
                    lines.append(f"              {entry.line.strip()}")
            lines.append("          ^ STUCK HERE - bhej dena ye lines")

        print("\n" + "\n".join(lines))
        for line in lines:
            log("SLOW: " + line.strip())

    timer = threading.Timer(seconds, bark)
    timer.daemon = True
    timer.start()
    return timer


def handle(text: str) -> None:
    """One command, start to finish."""
    global PENDING_CONFIRMATION
    text = (text or "").strip()
    if not text:
        return

    compound = router.split_compound(text)
    if len(compound) > 1:
        print(f'  compound  {len(compound)} safe actions')
        for part in compound:
            handle(part)
        return

    if PENDING_CONFIRMATION is not None:
        answer = text.lower().strip()
        if answer in {"yes", "haan", "ha", "confirm", "do it", "kar do"}:
            intent, raw = PENDING_CONFIRMATION
            PENDING_CONFIRMATION = None
            result = actions.press_keys(intent.term)
            print(f"  {'press' if result.ok else 'fail '}  {result.say}")
            log(f"  {'ok' if result.ok else 'FAIL'}: {result.say}  {result.detail}")
            if result.ok and config.SPEAK_KEY_ACTIONS:
                speak.say(result.say)
            return
        if answer in {"no", "nahi", "nahin", "cancel", "stop"}:
            PENDING_CONFIRMATION = None
            print("  cancel  nahi kiya")
            speak.say("theek hai, cancel kar diya")
            return
        print("  confirm  haan ya nahi bolo")
        speak.say("haan ya nahi bolo")
        return

    print(f'\n  heard   "{text}"')
    started = time.perf_counter()
    intent = router.route(text)
    took = (time.perf_counter() - started) * 1000

    # Show the repair when there was one, so a misfire is traceable to the
    # transcript rather than to the router.
    fixed = router.normalize(text)
    if fixed != text.lower():
        print(f'  fixed   "{fixed}"')

    conf = f" p={intent.confidence:.2f}" if intent.confidence else ""
    print(f"  intent  {intent.name}  type={intent.file_type}  "
          f'term="{intent.term}"  via {intent.source}{conf}  {took:.0f}ms')
    log(f'heard="{text}" fixed="{fixed}" -> {intent.name} '
        f'term="{intent.term}" via {intent.source}')

    if DRY_RUN:
        preview = actions.preview(intent, raw_text=fixed)
        print(f"  dry     {preview.say}")
        if preview.options:
            for alt in preview.options:
                print(f"          also: {alt}")
        return

    guard = _watchdog(f"{intent.name} term={intent.term!r}")
    try:
        result = actions.run(intent, raw_text=fixed)
    finally:
        guard.cancel()
    verb = {"press_keys": "press", "window_action": "window"}.get(intent.name, "open ")
    print(f"  {verb if result.ok else 'fail '}  {result.say}")
    log(f"  {'ok' if result.ok else 'FAIL'}: {result.say}  {result.detail}")
    if result.detail:
        print(f"          {result.detail}")
    if result.options:
        for alt in result.options:
            print(f"          also: {alt}")

    if result.confirmation_required:
        PENDING_CONFIRMATION = (intent, fixed)
        speak.say(result.say)
        return

    # A slide change every few seconds does not need narrating mid-talk.
    if result.ok and intent.name == "press_keys" and not config.SPEAK_KEY_ACTIONS:
        return
    speak.say(result.say)


# ------------------------------------------------------------------ modes
def text_mode() -> None:
    print("Type a command, or 'q' to quit.\n")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line.lower() in {"q", "quit", "exit"}:
            break
        handle(line)


class Gate:
    """Decides whether a new voice command may start while one is running.

    Pulled out of voice_mode as a real object for one reason: this is the
    part that was broken, and as a closure there was no way to test it
    without a microphone. The rule it encodes is small but it is the
    difference between a session that recovers and one that dies.

    Before, commands ran while holding a plain lock and the tap handlers did
    `if busy.locked(): return` - silently. One command that never came back
    therefore swallowed every command after it, printing nothing. That turns
    any single slow or wedged action into "LAYA is dead", which is what
    Harsh was seeing and the only part of it that was certainly a bug rather
    than a theory.

    Two changes. A tap during a normal command gets told to wait instead of
    being dropped in silence. And past `stuck_after` the running command
    stops being deferred to at all: it cannot be killed - Python has no way
    to do that safely - but it can stop being a reason to ignore the person
    talking.
    """

    def __init__(self, stuck_after: float = 20.0) -> None:
        self._lock = threading.Lock()
        self._started_at: float | None = None
        self.stuck_after = stuck_after

    def running_for(self) -> float:
        """Seconds the current command has been going. 0 if none is."""
        started = self._started_at
        return 0.0 if started is None else time.perf_counter() - started

    def reject(self) -> bool:
        """True if a new command should be refused. Explains itself aloud."""
        if not self._lock.locked():
            return False

        waited = self.running_for()
        if waited < self.stuck_after:
            print(f"\n  busy    ek command chal raha hai ({waited:.0f}s) - ruko")
            return True

        print(f"\n  stuck   pichla command {waited:.0f}s se atka hai; "
              f"naya chala raha hoon")
        log(f"STUCK: previous command wedged {waited:.0f}s - running anyway")
        return False

    def start(self) -> bool:
        """Claim the session. False means someone else holds it."""
        if not self._lock.acquire(blocking=False):
            return False
        self._started_at = time.perf_counter()
        return True

    def finish(self) -> None:
        self._started_at = None
        try:
            self._lock.release()
        except RuntimeError:
            pass


def voice_mode() -> None:
    import listen

    mic = listen.Microphone()
    stt = listen.Transcriber()
    gate = Gate()
    recording = threading.Lock()
    hold_active = False  # True only when on_start actually fired

    # Warm the model up now so the first real command isn't the slow one.
    threading.Thread(target=stt.load, daemon=True).start()

    def refuse() -> bool:
        return recording.locked() or gate.reject()

    def transcribe_and_run(audio) -> None:
        def work() -> None:
            # Never block on the gate: if the last command is wedged we have
            # already decided to go ahead without waiting for it.
            mine = gate.start()
            try:
                said = stt.transcribe(audio)
                if not said.strip():
                    # Say so out loud. A clip that produces no text used
                    # to end here in total silence - nothing printed,
                    # nothing logged, nothing spoken - so a one-word
                    # command like "save" that the mic missed looked
                    # exactly like a command that ran and did nothing.
                    # Two different bugs, and no way to tell them apart.
                    why = stt.last_skip or "no speech found"
                    print(f"\n  nothing  ({why})")
                    log(f"EMPTY transcript: {why}")
                    speak.say("kuch sunai nahi diya, phir se bolo")
                    return
                handle(said)
            except Exception as exc:
                print(f"\n  error   {type(exc).__name__}: {exc}")
                log(f"ERROR {type(exc).__name__}: {exc}")
            finally:
                if mine:
                    gate.finish()

        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------- tap handlers
    def capture() -> None:
        """Record in its own thread, never in the key handler.

        Recording blocks for up to TAP_MAX_SECONDS. Doing that inside
        pynput's callback would stall the global keyboard hook, and
        Windows then lags every key you press anywhere.
        """
        with recording:
            print("\n  [listening...]", end="", flush=True)
            log("tap - recording")
            audio = mic.record_until_silence()
            seconds = audio.size / config.SAMPLE_RATE
            print(f" {seconds:.1f}s")
            log(f"captured {seconds:.1f}s")
            if audio.size == 0:
                print("  (nothing recorded - wrong mic? run python mics.py)")
                return
        transcribe_and_run(audio)

    def on_tap() -> None:
        if refuse():
            return
        threading.Thread(target=capture, daemon=True).start()

    # ---------------------------------------------------------- hold handlers
    def on_start() -> None:
        nonlocal hold_active
        if refuse():
            return
        hold_active = True
        print("\n  [listening - release to run]", end="", flush=True)
        log("hotkey down - recording")
        mic.start()

    def on_stop() -> None:
        nonlocal hold_active
        if not hold_active:
            return  # on_start was blocked; nothing to stop
        hold_active = False
        audio = mic.stop()
        seconds = audio.size / config.SAMPLE_RATE
        log(f"hotkey up - captured {seconds:.1f}s")
        if audio.size == 0:
            print("\n  (nothing recorded - mic muted, or hotkey released too fast)")
            return
        transcribe_and_run(audio)

    # ---------------------------------------------------------- launch
    use_tap  = config.TRIGGER in ("tap", "both")
    use_hold = config.TRIGGER in ("hold", "both")

    if use_tap:
        print(f"  earbud   tap once, speak, stop.  (key: {config.TAP_KEY})")
    if use_hold:
        combo = " + ".join(sorted(config.HOTKEY)).title()
        print(f"  hotkey   hold {combo}, speak, release.")
    print("  Ctrl+C to quit.\n")

    # Tap listener in background thread, hold in main (or vice versa).
    if use_tap and use_hold:
        tap = listen.TapToTalk(on_tap)
        threading.Thread(target=tap.run, daemon=True).start()
        try:
            listen.PushToTalk(on_start, on_stop).run()
        except KeyboardInterrupt:
            pass
    elif use_tap:
        try:
            listen.TapToTalk(on_tap).run()
        except KeyboardInterrupt:
            pass
    else:
        try:
            listen.PushToTalk(on_start, on_stop).run()
        except KeyboardInterrupt:
            pass


# ------------------------------------------------------------------ entry
def banner() -> None:
    es = search.es_path()
    print("=" * 58)
    print("  Sparrow")
    print(f"  search   {'Everything (' + es.name + ')' if es else 'built-in index (no es.exe)'}")
    print(f"  device   {config.DEVICE}")
    print("=" * 58)


def main() -> None:
    global DRY_RUN
    args = sys.argv[1:]

    # Line-buffer the console. When stdout is a pipe rather than a terminal -
    # piped to a file, or read by another tool - Python block-buffers it, and
    # then the last 8 KB of output simply is not there yet. A command that
    # ran fine looks exactly like one that hung, which is a diagnosis problem
    # dressed up as a bug and has already cost time here.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass

    if "--dry" in args:
        DRY_RUN = True
        args.remove("--dry")
    if "--quiet" in args:
        config.SPEAK_REPLIES = False
        args.remove("--quiet")

    if "--reindex" in args:
        print("Indexing...")
        print(f"Indexed {search.rebuild_index()} files.")
        return

    banner()

    # Start the speech worker before any command can arrive. Its first act
    # is to import pyttsx3/comtypes, which holds the import lock long
    # enough to wedge a command running in another thread - see speak.warm.
    speak.warm()

    if "--text" in args:
        args.remove("--text")
        if args:
            handle(" ".join(args))
        else:
            text_mode()
        return

    if args:  # one-shot: python main.py "open chrome"
        handle(" ".join(args))
        return

    voice_mode()


if __name__ == "__main__":
    try:
        main()
    finally:
        speak.stop()
