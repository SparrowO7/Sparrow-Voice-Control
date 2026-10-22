"""Which keystroke injection method actually reaches a real window?

Run this when a key command "does nothing". It is the only way to tell a
routing bug from a delivery bug, and they look identical from the outside:
the console prints "press  save" either way, because keys.press() returns
True as soon as pynput accepted the call - not when Windows acted on it.

The test target is Notepad, chosen because both answers are observable
without a human watching: Ctrl+S makes a window called "Save As" appear,
and Win+Down sets the minimised flag, and both can be read back over the
same win32 calls the rest of Sparrow uses.

    python diag_keys.py

Opens Notepad, tries each method, closes everything it opened.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import subprocess
import time

user32 = ctypes.windll.user32


# ---------------------------------------------------------------- observing
def _title(handle: int) -> str:
    buffer = ctypes.create_unicode_buffer(300)
    user32.GetWindowTextW(handle, buffer, 300)
    return buffer.value or ""


def front_title() -> str:
    return _title(user32.GetForegroundWindow())


def find_window(fragment: str) -> int:
    """First visible top-level window whose title contains fragment."""
    found = []
    fragment = fragment.lower()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND,
                        ctypes.wintypes.LPARAM)
    def visit(handle, _param):
        if user32.IsWindowVisible(handle) and fragment in _title(handle).lower():
            found.append(handle)
            return False
        return True

    user32.EnumWindows(visit, 0)
    return found[0] if found else 0


def is_minimised(handle: int) -> bool:
    return bool(user32.IsIconic(handle))


def wait_for_title(fragment: str, timeout: float = 2.5) -> str:
    """Poll the foreground title until it contains fragment. Returns what it saw.

    Polling rather than one sleep: a dialog that takes 300 ms to appear and a
    keystroke that never arrives are different outcomes, and a fixed sleep
    reports them the same way.
    """
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        last = front_title()
        if fragment.lower() in last.lower():
            return last
        time.sleep(0.1)
    return last


# ---------------------------------------------------------------- injecting
def send_pynput(spec: str) -> bool:
    """What Sparrow does today."""
    import keys

    return keys.press(spec)


def send_pyautogui(spec: str) -> bool:
    """What type_text does today - and type_text is the part that works."""
    try:
        import pyautogui

        parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
        pyautogui.hotkey(*parts)
        return True
    except Exception as exc:
        print(f"      pyautogui raised {type(exc).__name__}: {exc}")
        return False


# Virtual-key codes, for the one method that cannot be blamed on a library.
_VK = {
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
    "down": 0x28, "up": 0x26, "left": 0x25, "right": 0x27,
    "f4": 0x73, "tab": 0x09, "esc": 0x1B, "enter": 0x0D,
    "s": 0x53, "t": 0x54, "w": 0x57, "a": 0x41, "d": 0x44,
}
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_EXTENDEDKEY = 0x0001
_EXTENDED = {"up", "down", "left", "right", "win"}


def send_keybd_event(spec: str) -> bool:
    """Raw win32, with real virtual-key codes and the extended-key flag.

    This exists to settle the argument. If a combo lands here and nowhere
    else, the problem is the library's injection method, not Windows, not
    focus, and not the router.
    """
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    codes = [_VK.get(p) for p in parts]
    if any(code is None for code in codes):
        print(f"      no vk for one of {parts}")
        return False

    def flags(name: str) -> int:
        return _KEYEVENTF_EXTENDEDKEY if name in _EXTENDED else 0

    for name, code in zip(parts, codes):
        user32.keybd_event(code, 0, flags(name), 0)
        time.sleep(0.02)
    for name, code in reversed(list(zip(parts, codes))):
        user32.keybd_event(code, 0, flags(name) | _KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)
    return True


METHODS = {
    "pynput (current)": send_pynput,
    "pyautogui": send_pyautogui,
    "keybd_event": send_keybd_event,
}


# ---------------------------------------------------------------- the tests
def open_notepad() -> int:
    """A fresh Notepad in front, or 0. Everything else depends on this."""
    subprocess.Popen("notepad", shell=True)
    deadline = time.time() + 6
    while time.time() < deadline:
        handle = find_window("Notepad")
        if handle:
            user32.SetForegroundWindow(handle)
            time.sleep(0.4)
            return handle
        time.sleep(0.2)
    return 0


def test_save(send, name: str) -> bool:
    """Ctrl+S in Notepad. The Save As dialog appearing is the proof."""
    notepad = find_window("Notepad")
    if not notepad:
        print("      lost Notepad")
        return False
    user32.SetForegroundWindow(notepad)
    time.sleep(0.3)

    sent = send("ctrl+s")
    saw = wait_for_title("Save")
    worked = "save" in saw.lower()
    print(f"      sent={sent}  foreground now: {saw!r}  ->  "
          f"{'WORKS' if worked else 'nothing happened'}")

    if worked:  # dismiss the dialog so the next method starts clean
        send_keybd_event("esc")
        time.sleep(0.5)
    return worked


def test_minimise(send, name: str) -> bool:
    """Win+Down on Notepad. The minimised flag is the proof."""
    notepad = find_window("Notepad")
    if not notepad:
        print("      lost Notepad")
        return False
    user32.SetForegroundWindow(notepad)
    time.sleep(0.3)
    if is_minimised(notepad):
        user32.ShowWindow(notepad, 9)  # SW_RESTORE
        time.sleep(0.4)

    sent = send("win+down")
    deadline = time.time() + 2.0
    worked = False
    while time.time() < deadline:
        if is_minimised(notepad):
            worked = True
            break
        time.sleep(0.1)
    print(f"      sent={sent}  minimised={worked}  ->  "
          f"{'WORKS' if worked else 'nothing happened'}")

    if worked:
        user32.ShowWindow(notepad, 9)
        time.sleep(0.4)
    return worked


TESTS = (("ctrl+s  (save)", test_save), ("win+down  (minimize)", test_minimise))


def main() -> None:
    print("=" * 62)
    print("  keystroke delivery check")
    print("=" * 62)

    notepad = open_notepad()
    if not notepad:
        print("  could not open Notepad - nothing to test against.")
        return
    print(f"  target   {_title(notepad)!r}  (hwnd {notepad})\n")

    scores: dict[str, list[str]] = {}
    for label, test in TESTS:
        print(f"  {label}")
        for name, send in METHODS.items():
            print(f"    {name}")
            if test(send, name):
                scores.setdefault(name, []).append(label)
        print()

    print("=" * 62)
    for name in METHODS:
        got = scores.get(name, [])
        print(f"  {name:20} {len(got)}/{len(TESTS)}  {', '.join(got) or '-'}")
    print("=" * 62)

    # Close the Notepad this script opened. No save prompt: nothing was typed.
    notepad = find_window("Notepad")
    if notepad:
        user32.PostMessageW(notepad, 0x0010, 0, 0)  # WM_CLOSE
        print("  cleaned up Notepad.")


if __name__ == "__main__":
    main()
