"""close_app, run against real windows. The other half of the close fix.

    set PYTHONIOENCODING=utf-8 && python test_close_app.py

Routing was already proved - "close notepad" comes back close_app and not
open_app. What was never run is the action: whether a window actually goes
away, whether a save prompt is reported instead of being claimed as success,
and whether the console refuses to close itself.

SAFETY. close_app closes EVERY window of the app it is given, which is what
you want from "close chrome" and is not something a test may do to your
desktop. So each case here opens its own window and, when windows of that
app were already open, substitutes the inventory so only the test's own
window is reachable. Which mode each case ran in is printed - an unpatched
run exercises the real state.windows_of enumeration and is the stronger
test, so the script says when it got one.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path

import actions
import router
import state

_results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    _results.append((bool(ok), label))
    print(f"{'  ok  ' if ok else '  FAIL'}  {label}")
    if detail:
        for line in str(detail).splitlines():
            print(f"          {line}")
    return bool(ok)


class only_these:
    """Hide every window except `handles` from state.windows_of.

    Used only when windows of the app under test were already open, so the
    test cannot close a document of yours. _pick_target, state.close and the
    verification all still run for real.
    """

    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        self.real = state.windows_of
        rows = self.rows
        # _exists is re-checked on every call, not baked in when the patch is
        # installed. Without it the substituted list still names a window
        # that has since closed, close_app re-enumerates to confirm, sees it
        # "still open" and reports a failure that did not happen - which is
        # exactly what this harness said the first time it ran.
        state.windows_of = lambda names: [
            row for row in rows
            if any(n.strip().lower() == row[2] for n in names)
            and state._exists(row[0])]
        return self

    def __exit__(self, *_exc):
        state.windows_of = self.real
        return False


def _scratch_notepad() -> tuple[int, Path]:
    """A Notepad window this test owns, on a temp file. (handle, path)."""
    path = Path(tempfile.gettempdir()) / f"laya_close_{int(time.time()*1000)}.txt"
    path.write_text("", encoding="utf-8")
    subprocess.Popen(["notepad.exe", str(path)])
    stem = path.stem.lower()
    for _ in range(40):
        time.sleep(0.25)
        for handle, title, _exe in state.windows_of(["notepad"]):
            if stem in title.lower():
                return handle, path
    return 0, path


# ------------------------------------------------------- app that is not open
def test_not_open() -> None:
    """Nothing to close is an answer, not a launch."""
    print("\n  1) app that is not running")
    for name in ("spotify", "winword", "mspaint"):
        if state.windows_of([name]):
            print(f"          skipping {name} - it is actually open")
            continue
        result = actions.close_app(name)
        check(not result.ok, f"{name}: refused", result.say)
        check("khula" in result.say, f"{name}: says it was not open", result.say)
        # The thing the old behaviour did wrong: it opened it.
        check(not state.windows_of([name]),
              f"{name}: and did NOT launch it as a side effect")
        break


# ------------------------------------------------------------- the real close
def test_closes_notepad() -> None:
    """A window named out loud actually goes away."""
    print("\n  2) close notepad -> the window is gone")
    others = state.windows_of(["notepad"])
    handle, path = _scratch_notepad()
    if not check(bool(handle), "opened a scratch notepad", path.name):
        path.unlink(missing_ok=True)
        return

    row = [(handle, path.name, "notepad")]
    patched = bool(others)
    print(f"          mode: {'inventory substituted - you had notepad open'
                             if patched else 'UNPATCHED - real enumeration'}")

    intent = router.route("close notepad")
    check(intent.name == "close_app" and intent.term == "notepad",
          "routes to close_app", f"{intent.name} term={intent.term!r}")

    if patched:
        with only_these(row):
            result = actions.close_app(intent.term)
    else:
        result = actions.close_app(intent.term)

    check(result.ok, "reported success", f"{result.say}\n{result.detail}")
    time.sleep(0.6)
    check(not state._exists(handle), "the window no longer exists",
          f"hwnd={handle}")
    path.unlink(missing_ok=True)


# --------------------------------------------------------- unsaved, not lost
def test_unsaved_not_lost() -> None:
    """Closing an unsaved file must not silently destroy the text.

    This case was written expecting a save prompt, because that is what
    state.close's docstring says happens. Measured on this machine, it does
    not: Windows 11 Notepad answers WM_CLOSE by closing, with no #32770
    dialog and no asterisk left anywhere to find.

        dirty title  : '*restore_1791289471.txt - Notepad'
        closed       : True
        reopened     : *restore_1791289471.txt - Notepad
        contents     : 'SURVIVE CHECK 12345'

    The text is not gone - Notepad keeps the unsaved buffer in its own
    session store and hands it back, still marked unsaved, when the file is
    reopened. So "close notepad" is safe on an unsaved file, and the thing
    worth testing is that property rather than a dialog this OS never shows.

    close_app's dialogs branch is therefore NOT exercised here. It is still
    reachable by apps that do prompt, and the same #32770 check is covered on
    the press_keys path by "save" opening Save As.
    """
    print("\n  3) unsaved notepad -> closes, and the text is recoverable")
    handle, path = _scratch_notepad()
    if not check(bool(handle), "opened a scratch notepad", path.name):
        path.unlink(missing_ok=True)
        return

    marker = f"unsaved survives {int(time.time()) % 10000}"
    typed = actions.type_text(marker, target="notepad")
    check(typed.ok, "typed into it", typed.say)
    time.sleep(0.6)
    check(state._title_of(handle).startswith("*"),
          "notepad marks it unsaved", repr(state._title_of(handle)))

    with only_these([(handle, path.name, "notepad")]):
        result = actions.close_app("notepad")

    check(result.ok, "reported honestly", f"{result.say}\n{result.detail}")
    time.sleep(0.6)
    check(not state._exists(handle), "the window closed")

    # The safety property: reopen the same path and the text comes back.
    subprocess.Popen(["notepad.exe", str(path)])
    time.sleep(3.0)
    rows = [row for row in state.windows_of(["notepad"])
            if path.stem.lower() in row[1].lower()]
    if check(bool(rows), "reopens on the same path"):
        import pyautogui
        import pyperclip

        state._raise(rows[0][0])
        time.sleep(0.8)
        pyperclip.copy("")
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.2)
        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.6)
        body = pyperclip.paste() or ""
        check(marker in body, "the unsaved text was NOT lost",
              f"contents={body[:50]!r}")
        try:
            state.close(rows[0][0])
            time.sleep(0.8)
        except Exception:
            pass
    path.unlink(missing_ok=True)


# ------------------------------------------------------- the console refusal
def test_console_refused() -> None:
    """close_app must never close the session that was asked to run it."""
    print("\n  4) the console refuses to close itself")
    handle, path = _scratch_notepad()
    if not check(bool(handle), "opened a stand-in window", path.name):
        path.unlink(missing_ok=True)
        return

    # Tell actions this window IS the console. Nothing else is substituted,
    # so the refusal has to come from close_app's own check.
    real = state.console_handle
    try:
        state.console_handle = lambda: handle
        with only_these([(handle, path.name, "notepad")]):
            result = actions.close_app("notepad")
    finally:
        state.console_handle = real

    check(not result.ok, "refused", result.say)
    check("console" in result.say.lower(), "says why", result.say)
    check(state._exists(handle), "and the window is untouched")

    try:
        state.close(handle)
        time.sleep(0.5)
    except Exception:
        pass
    path.unlink(missing_ok=True)


def main() -> None:
    print("=" * 70)
    print("  close_app against real windows")
    print("=" * 70)

    test_not_open()
    test_closes_notepad()
    test_unsaved_not_lost()
    test_console_refused()

    bad = [label for ok, label in _results if not ok]
    print("\n" + "=" * 70)
    print(f"  {len(_results) - len(bad)}/{len(_results)} passed")
    for label in bad:
        print(f"    FAILED: {label}")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
