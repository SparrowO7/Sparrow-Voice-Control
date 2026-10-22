"""Send keystrokes to whatever window is in front.

This is the presentation half of the bot. "agla slide" does not open
anything - it presses Right in PowerPoint, or in a PDF, or in Google Slides
inside Chrome. Keystrokes work in all three; PowerPoint COM automation
would only work in one, which is why this is the approach.

Focus matters: the keys land wherever Windows thinks you are typing. The
hotkey listener is global and the launcher starts minimised, so the window
you clicked last - your slides - keeps focus and gets the key.
"""

from __future__ import annotations

import time

_KEY_CACHE: dict[str, object] = {}

# Spoken-friendly names for keys pynput calls something else.
_ALIASES = {
    "pagedown": "page_down", "pgdn": "page_down",
    "pageup": "page_up", "pgup": "page_up",
    "esc": "esc", "escape": "esc",
    "return": "enter",
    "arrow_right": "right", "arrow_left": "left",
    "volup": "media_volume_up", "voldown": "media_volume_down",
    "mute": "media_volume_mute",
}

_MODIFIERS = {"ctrl", "alt", "shift", "cmd", "win"}


def _controller():
    """One Controller, created on first use - importing pynput is not free."""
    if "controller" not in _KEY_CACHE:
        from pynput.keyboard import Controller

        _KEY_CACHE["controller"] = Controller()
    return _KEY_CACHE["controller"]


def _resolve(name: str):
    """Turn 'right' or 'f5' or 'b' into something pynput can press."""
    from pynput.keyboard import Key

    name = _ALIASES.get(name, name)
    if name == "win":
        name = "cmd"
    special = getattr(Key, name, None)
    if special is not None:
        return special
    if len(name) == 1:
        return name  # a plain character
    return None


def press(spec: str, repeat: int = 1) -> bool:
    """Press a combo like 'right', 'f5', 'ctrl+shift+tab'. False if unknown."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        return False

    mods = [_resolve(p) for p in parts if p in _MODIFIERS]
    mains = [_resolve(p) for p in parts if p not in _MODIFIERS]
    if not mains or any(k is None for k in mods + mains):
        return False

    board = _controller()
    # Give the foreground window a moment - we were just called from a
    # key-release handler, and pressing into that same instant gets eaten.
    time.sleep(0.05)

    try:
        for _ in range(max(1, repeat)):
            for mod in mods:
                board.press(mod)
            for key in mains:
                board.press(key)
                board.release(key)
            for mod in reversed(mods):
                board.release(mod)
            if repeat > 1:
                time.sleep(0.12)
        return True
    except Exception:
        # Never let a failed keypress take the whole bot down.
        for mod in reversed(mods):
            try:
                board.release(mod)
            except Exception:
                pass
        return False


def foreground_window() -> str:
    """Title of the window that will receive the keys. For the log line.

    Borrowed from state rather than read here, because reading it here is
    what wedged the session: GetWindowTextW blocks until the owning process
    answers, with no timeout, and this runs before every single keystroke.
    A log line is never worth hanging the program for - hence the fallback
    rather than a raise.
    """
    try:
        import state

        return state.window_title() or "(no title)"
    except Exception:
        return "(unknown)"
