"""type_text target resolution and the console guard, measured.

    set PYTHONIOENCODING=utf-8 && python test_type_target.py

The bug this covers: Notepad was open, the console was in front because you
had just finished speaking to LAYA, and type_text refused. The refusal was
right about the console and wrong about giving up - the window it wanted was
already on the desktop, one Z-order position back.

Four cases, from your list:

  a) Notepad open, console in front   -> focuses Notepad and types there
  b) no editor open at all            -> refuses, same as before
  c) "type on notepad hello world"    -> types "hello world", not the target
  d) the console                      -> never receives text, ever

(b) and (d) are simulated by swapping state.windows_of rather than by
closing your windows. Closing them would be a test that costs you unsaved
work to run, and the thing under test is what type_text does with the list
it is given - which a substituted list exercises exactly.

(a) is real: it opens its own Notepad, types into it, reads the text back
out through the clipboard, and closes only the window it opened.
"""

from __future__ import annotations

import time

import actions
import config
import keys
import router
import state

PASS, FAIL = "  ok  ", "  FAIL"
_results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    _results.append((bool(ok), label))
    print(f"{PASS if ok else FAIL}  {label}")
    if detail:
        for line in str(detail).splitlines():
            print(f"          {line}")
    return bool(ok)


# ----------------------------------------------------------------- c) parsing
def test_explicit_target() -> None:
    """The named window must not end up inside the typed line."""
    print("\n  c) explicit target syntax")
    cases = [
        ("type on notepad hello world", "notepad", "hello world"),
        ("type in notepad hello world", "notepad", "hello world"),
        ("notepad mein type karo hello world", "notepad", "hello world"),
        ("notepad me likho hello world", "notepad", "hello world"),
        ("type on notepad the quick brown fox", "notepad", "the quick brown fox"),
        ("type into vs code print hello", "code", "print hello"),
        # No preposition means no target - "notepad" is a word being
        # dictated, not the window. This is the line that keeps the feature
        # from eating ordinary text.
        ("type notepad is slow", "", "notepad is slow"),
        ("type hello this is laya", "", "hello this is laya"),
    ]
    for text, want_target, want_payload in cases:
        intent = router.route(text)
        got_target = getattr(intent, "target", "")
        ok = got_target == want_target and intent.term == want_payload
        check(ok, f"{text!r}",
              "" if ok else f"target {got_target!r} (want {want_target!r})\n"
                            f"text   {intent.term!r} (want {want_payload!r})")


# --------------------------------------------------------------- d) the console
# GetConsoleWindow answers for the calling process, so it returns a real
# handle when you run `python main.py` in a terminal and 0 when this file is
# run from a tool that gave python no console. That makes "put the console in
# front" impossible to stage here - so the tests below stage the CONDITION
# instead, by telling actions that whatever window is in front right now is
# the console.
#
# Only the identification is substituted, and it is the one part that is
# environment-dependent. _refusal, _pick_target, windows_of,
# _focus_for_typing and _send_text all run for real, which is where the bug
# was.
class pretend_front_is_console:
    """Make the window currently in front the one type_text must refuse."""

    def __enter__(self):
        self.real = state.console_handle
        self.handle = state._foreground_handle()
        state.console_handle = lambda: self.handle
        return self.handle

    def __exit__(self, *_exc):
        state.console_handle = self.real
        return False


def test_console_protected() -> None:
    """The console is refused by handle, and by process as a backstop."""
    print("\n  d) console is never a target")
    handle = state.console_handle()
    if handle:
        check(bool(actions._refusal(handle, "python")),
              "refused by its own handle (GetConsoleWindow)",
              f"hwnd={handle}  {state._title_of(handle)[:40]!r}")
    else:
        print("          note: no console window for this process "
              "(expected when not run from a terminal)")
        with pretend_front_is_console() as fake:
            check(bool(actions._refusal(fake, "notepad")),
                  "refused by handle even when the exe looks safe",
                  f"hwnd={fake}")
    for exe in ("python", "powershell", "cmd", "windowsterminal", "conhost"):
        check(bool(actions._refusal(0, exe)), f"refused by process: {exe}")
    # And the part the old title check got wrong in the other direction: a
    # Notepad file called "python notes.txt" used to be refused.
    check(not actions._refusal(0, "notepad"),
          "notepad is allowed - the title is not consulted at all")


def test_console_never_typed() -> None:
    """Protected window in front, nothing else open. Must not send keys."""
    print("\n  d) protected front + no editor -> sends nothing")
    sent: list[str] = []
    real_send, real_windows = actions._send_text, state.windows_of
    try:
        actions._send_text = lambda text: sent.append(text)
        state.windows_of = lambda names: []          # nothing open anywhere
        with pretend_front_is_console():
            result = actions.type_text("rm -rf something")
        check(not sent, "no keystrokes were sent", f"sent={sent}")
        check(not result.ok, "reported failure", result.say)
    finally:
        actions._send_text, state.windows_of = real_send, real_windows


# -------------------------------------------------------------- b) nothing open
def test_no_editor_open() -> None:
    """The original refusal still happens when there is genuinely nowhere."""
    print("\n  b) no editor open -> same refusal as before")
    real_windows = state.windows_of
    try:
        state.windows_of = lambda names: []
        with pretend_front_is_console():
            result = actions.type_text("hello")
        check(not result.ok, "refused", result.say)
        check("kholo" in result.say.lower(),
              "still asks you to open one", result.say)
    finally:
        state.windows_of = real_windows


# ------------------------------------------------- a) notepad behind the console
def _own_notepad(path) -> int:
    """Open `path` in Notepad and return that window's handle, or 0.

    Its own file, not a bare Notepad, and that is a correctness fix rather
    than tidiness. The first version of this test called open_app("notepad"),
    which correctly reused the Notepad that was already open - and then typed
    its marker into the document that was in it. Nothing reached disk, but a
    test that edits whatever you happen to have open is not a test anyone
    should run twice.
    """
    import subprocess

    subprocess.Popen(["notepad.exe", str(path)])
    stem = path.stem.lower()
    for _ in range(40):
        time.sleep(0.25)
        for handle, title, _exe in state.windows_of(["notepad"]):
            if stem in title.lower():
                return handle
    return 0


def test_notepad_behind_console() -> None:
    """The reported bug: Notepad open, a protected window in front, type anyway."""
    print("\n  a) notepad open but behind a protected window")
    import tempfile
    from pathlib import Path

    marker = f"laya target test {int(time.time()) % 10000}"
    path = Path(tempfile.gettempdir()) / f"laya_type_{int(time.time())}.txt"
    path.write_text("", encoding="utf-8")

    keep_front = state._foreground_handle()
    handle = _own_notepad(path)
    if not check(bool(handle), "opened a scratch notepad", f"{path.name}"):
        path.unlink(missing_ok=True)
        return

    # Put something else in front, so the command starts from the situation
    # you reported rather than from Notepad already being focused - which
    # would prove nothing.
    state._raise(keep_front)
    time.sleep(0.6)
    check(state._owner(state._foreground_handle()) != "notepad",
          "notepad is NOT the window in front",
          f"front exe={state._owner(state._foreground_handle())}")

    # Only this test's window is offered to the picker. The real _pick_target,
    # _focus_for_typing and _send_text all run; what is substituted is the
    # inventory, so the test can never reach a document of yours.
    real_windows = state.windows_of
    try:
        state.windows_of = lambda names: (
            [(handle, path.name, "notepad")]
            if any(n.lower() == "notepad" for n in names) else [])
        with pretend_front_is_console():
            result = actions.type_text(marker)
    finally:
        state.windows_of = real_windows

    check(result.ok, "type_text reported success",
          f"{result.say}\n{result.detail}")
    time.sleep(0.4)
    check(state._foreground_handle() == handle,
          "the scratch notepad is the window in front now",
          f"front={state.window_title()[:44]!r}")

    # Verified through the file rather than the clipboard: Ctrl+S writes to a
    # path this test owns, so reading it back proves the text went into THAT
    # window. The clipboard cannot say which window it came from.
    keys.press("ctrl+s")
    time.sleep(1.2)
    on_disk = ""
    try:
        on_disk = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        on_disk = f"(unreadable: {exc})"
    check(marker in on_disk, "the text is inside that notepad's file",
          f"{path.name} = {on_disk[:60]!r}")

    try:
        state._raise(handle)
        time.sleep(0.3)
        state.close()
        time.sleep(0.6)
    except Exception:
        pass
    path.unlink(missing_ok=True)


def main() -> None:
    print("=" * 70)
    print("  type_text: target resolution + console guard")
    print("=" * 70)
    print(f"  console hwnd : {state.console_handle()}")
    print(f"  auto targets : {', '.join(config.TYPE_AUTO_TARGETS[:5])} ...")

    test_explicit_target()
    test_console_protected()
    test_console_never_typed()
    test_no_editor_open()
    test_notepad_behind_console()

    bad = [label for ok, label in _results if not ok]
    print("\n" + "=" * 70)
    print(f"  {len(_results) - len(bad)}/{len(_results)} passed")
    for label in bad:
        print(f"    FAILED: {label}")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
