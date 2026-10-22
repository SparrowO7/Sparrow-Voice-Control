"""What is on screen right now, so a command can act on the current window.

Why this file exists: every command used to decide what to do from the
sentence alone. "go back" is a back-arrow slide key in PowerPoint and
Alt+Left in a browser, and the sentence is identical in both - only the
window in front can tell them apart. Same for "search for X": if a browser
is already open, the search belongs in that one, not in a fresh launch.

Three questions get asked of this module:

    front()          - which window has focus, and which program owns it
    is_browser()     - is that program a browser
    browser_command()- the browser-native key for a direction, or None

State is read live from Windows each time rather than cached. A cache goes
stale the moment you Alt+Tab, and a stale answer here means a keystroke
into the wrong window. What IS remembered is Sparrow's own side of the story
- what it launched, what it clicked - because nothing in Windows reports it.
"""

from __future__ import annotations

import time

_WINDOW_TITLE_MAX = 300

# Process names, not spoken aliases: this is what Windows reports, and it is
# the only spelling that cannot drift. ".exe" is stripped before comparing.
_BROWSER_EXES = {
    "chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "chromium",
    "librewolf", "waterfox", "arc",
}

# A browser's own navigation keys. These are the ones that mean "back" and
# "forward" inside a browser, as opposed to the arrow keys a slide deck
# wants. Chrome, Edge, Brave and Firefox all accept Alt+Left/Right.
_BROWSER_NAV = {
    "left": "alt+left",
    "right": "alt+right",
}


def _foreground_handle() -> int:
    """The HWND in front, or 0."""
    try:
        import ctypes

        return ctypes.windll.user32.GetForegroundWindow()
    except Exception:
        return 0


def window_title() -> str:
    """Title of the window in front. This is the real one, not a guess.

    Through _title_of for the same reason _enumerate is - see the note
    there. This one is worth saying twice because of where it is called
    from: after every keystroke, on every launch, and from the verification
    paths. It is the busiest reader of the title in the program, and it was
    asking the question the way that can never return.
    """
    handle = _foreground_handle()
    return _title_of(handle) if handle else ""


def process_name() -> str:
    """The exe behind the window in front, lowercase and without ".exe".

    Empty when it cannot be read, which is deliberately not the same as
    "not a browser" - callers that care should treat empty as unknown.
    """
    handle = _foreground_handle()
    if not handle:
        return ""
    try:
        import ctypes
        import ctypes.wintypes

        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(
            handle, ctypes.byref(pid)
        )
        if not pid.value:
            return ""

        import psutil

        name = psutil.Process(pid.value).name().lower()
        return name[:-4] if name.endswith(".exe") else name
    except Exception:
        return ""


def front() -> dict:
    """Everything a caller usually wants, read in one go.

    Two lookups rather than one because they fail independently: the title
    comes from user32 and the process from the process table, and a title
    with no process is still worth having for the spoken reply.
    """
    return {
        "title": window_title(),
        "process": process_name(),
        "at": time.time(),
    }


def is_browser(process: str | None = None) -> bool:
    """Is a browser in front, so browser keys are the ones you meant?

    Falls back to the window title when the process cannot be read, because
    a wrong "no" here sends Alt+Left into a browser as a slide-back arrow
    and nothing happens - the exact failure this module exists to stop.
    """
    name = process if process is not None else process_name()
    if name:
        return name in _BROWSER_EXES

    title = window_title().lower()
    return any(
        marker in title
        for marker in (" - google chrome", " - microsoft edge", " - brave",
                       " - firefox", " - chromium", " - vivaldi")
    )


def browser_command(direction: str) -> str | None:
    """The browser key for a direction, or None if no browser is in front."""
    if not is_browser():
        return None
    return _BROWSER_NAV.get(direction)


# ------------------------------------------------------------ window actions
# ShowWindow commands and the messages we need. Spelled out rather than
# imported so this file keeps working without pywin32 installed.
_SW_MAXIMIZE = 3
_SW_MINIMIZE = 6
_SW_RESTORE = 9
_WM_CLOSE = 0x0010

# Windows whose handles must never be minimised or closed. Progman and
# WorkerW *are* the desktop, and Shell_TrayWnd is the taskbar: closing any of
# them takes the shell down until you log out again. The foreground window is
# one of these more often than you would think - every time you click the
# wallpaper - so this is a real guard, not a theoretical one.
_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
                  "Button", "DV2ControlHost"}

# The last window Sparrow minimised. Windows does not record this anywhere, and
# without it "restore window" has nothing to act on: a minimised window has no
# focus, so there is no "current window" left to restore.
_last_minimised: int = 0


def _class_name(handle: int) -> str:
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(handle, buffer, 256)
        return buffer.value or ""
    except Exception:
        return ""


def _is_shell(handle: int) -> bool:
    """Is this the desktop or the taskbar rather than an app window?"""
    return _class_name(handle) in _SHELL_CLASSES


def _exists(handle: int) -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.user32.IsWindow(handle))
    except Exception:
        return False


def is_minimised(handle: int) -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.user32.IsIconic(handle))
    except Exception:
        return False


def is_maximised(handle: int) -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.user32.IsZoomed(handle))
    except Exception:
        return False


def _show(handle: int, command: int) -> bool:
    try:
        import ctypes

        ctypes.windll.user32.ShowWindow(handle, command)
        return True
    except Exception:
        return False


_WM_GETTEXT = 0x000D
_SMTO_ABORTIFHUNG = 0x0002
_send_timeout = None


def _title_reader():
    """SendMessageTimeoutW, set up once. None if it cannot be bound."""
    global _send_timeout
    if _send_timeout is None:
        try:
            import ctypes
            import ctypes.wintypes

            fn = ctypes.windll.user32.SendMessageTimeoutW
            fn.argtypes = [
                ctypes.wintypes.HWND, ctypes.c_uint, ctypes.c_size_t,
                ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                ctypes.POINTER(ctypes.c_size_t),
            ]
            fn.restype = ctypes.c_ssize_t
            _send_timeout = fn
        except Exception:
            _send_timeout = False
    return _send_timeout or None


def _title_of(handle: int, timeout_ms: int = 150) -> str:
    """A window's title, with a deadline. This is the hang that was open.

    GetWindowTextW is what the watchdog finally caught, and the stack said
    it exactly:

        state.py:233 in _enumerate()   user32.EnumWindows(visit, 0)
        state.py:224 in visit()        user32.GetWindowTextW(...)
        ^ STUCK HERE

    Asking another process for its window title sends it WM_GETTEXT and
    waits for its message loop to answer. There is no timeout. One app
    anywhere on the desktop that is busy, modal or wedged, and the whole
    enumeration stops there - so "notepad kholo" never returned, and the
    command after it never ran.

    That is also why three attempts failed to reproduce it. Nothing about
    it depends on LAYA: it depends on some unrelated program being busy at
    the moment you speak, which is not a thing a test reproduces on demand.
    Guessing was never going to find this; the watchdog was.

    SendMessageTimeoutW is the same question with a deadline, and
    SMTO_ABORTIFHUNG makes Windows answer immediately for a process it
    already knows is not responding. A title we could not read comes back
    empty, which costs us one window in a list rather than the session.
    """
    reader = _title_reader()
    if reader is None:
        return ""
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(_WINDOW_TITLE_MAX)
        got = ctypes.c_size_t(0)
        ok = reader(handle, _WM_GETTEXT, _WINDOW_TITLE_MAX,
                    ctypes.cast(buffer, ctypes.c_void_p),
                    _SMTO_ABORTIFHUNG, timeout_ms, ctypes.byref(got))
        return buffer.value if ok else ""
    except Exception:
        return ""


def _enumerate(deadline: float = 2.0) -> list[tuple[int, str]]:
    """(handle, title) for every visible top-level window with a title.

    The cheap non-blocking checks run first on purpose. IsWindowVisible and
    the class name are read from the window object and cannot stall; the
    title is the one that talks to another process. Filtering first means
    we only ask that question of the handful of windows that could matter
    instead of every window on the desktop.

    `deadline` is the backstop. Per-window timeouts still add up if enough
    apps are wedged at once, and a slow answer is worth far less than a
    session that keeps taking commands.
    """
    found: list[tuple[int, str]] = []
    try:
        import ctypes
        import ctypes.wintypes

        user32 = ctypes.windll.user32
        stop_at = time.perf_counter() + deadline

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND,
                            ctypes.wintypes.LPARAM)
        def visit(handle, _param):
            # IsWindowVisible stays true for a minimised window, which is
            # exactly what restore needs - it has to find windows that are
            # not on screen right now.
            if not user32.IsWindowVisible(handle) or _is_shell(handle):
                return True
            if time.perf_counter() > stop_at:
                return False
            title = _title_of(handle)
            if title:
                found.append((handle, title))
            return True

        user32.EnumWindows(visit, 0)
    except Exception:
        pass
    return found


def find(fragment: str) -> tuple[int, str]:
    """The window whose title contains `fragment`, or (0, "").

    Prefers the shortest matching title. "notepad" matches both "Untitled -
    Notepad" and "notepad.py - sparrow - Visual Studio Code", and the shorter
    one is nearly always the app itself rather than a file open inside
    something else.
    """
    fragment = (fragment or "").strip().lower()
    if not fragment:
        return 0, ""
    hits = [(h, t) for h, t in _enumerate() if fragment in t.lower()]
    if not hits:
        return 0, ""
    return min(hits, key=lambda row: len(row[1]))


def _raise(handle: int) -> bool:
    """Bring a window to the front, and return whether it actually came.

    This one call is behind most of the failure list. "open notepad" said
    "notepad khol raha hoon", Notepad launched, and the front window stayed
    Microsoft Edge - so "type hello this is LAYA" typed into Edge, "save"
    opened Edge's Save As, and "close window" closed that dialog. Every one
    of those looked like its own broken command. There was one bug.

    SetForegroundWindow on its own is why. Windows refuses it when the
    caller is not already the foreground process - it flashes the taskbar
    button instead, returns, and reports nothing. The documented way to
    actually get focus is to share an input queue with whoever currently
    has it, via AttachThreadInput, which is what the retry loop below does.

    Restoring first matters for the same reason it does in activate():
    focusing a minimised window succeeds and leaves it off the screen,
    which is indistinguishable from nothing happening.

    The return value is the point. It is read back from the OS, not
    assumed, so a caller can tell the truth about whether it worked.
    """
    if is_minimised(handle):
        _show(handle, _SW_RESTORE)
        time.sleep(0.2)

    for _ in range(3):
        if _attach_and_set(handle) and _holds(handle):
            return True
    return _foreground_handle() == handle


def _holds(handle: int, settle: float = 0.45) -> bool:
    """Is `handle` still in front a moment later?

    Checked after detaching, and that ordering is the whole reason this
    exists. Verifying while still attached to the other window's input
    queue said True and then focus fell back to the desktop about a second
    later - measured, twice in five tries, front coming back as "explorer".
    A verification that stops watching before the thing it verified comes
    undone is not a verification; it is the same optimistic guess with more
    steps.
    """
    deadline = time.perf_counter() + settle
    while time.perf_counter() < deadline:
        time.sleep(0.12)
        if _foreground_handle() != handle:
            return False
    return True


def _attach_and_set(handle: int) -> bool:
    """One attempt: borrow the foreground input queue, focus, give it back."""
    try:
        import ctypes

        user32 = ctypes.windll.user32
    except Exception:
        return False

    ours = theirs = 0
    attached = False
    try:
        ours = ctypes.windll.kernel32.GetCurrentThreadId()
        theirs = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(),
                                                 None)
        if theirs and theirs != ours:
            attached = bool(user32.AttachThreadInput(ours, theirs, True))
    except Exception:
        attached = False

    try:
        user32.BringWindowToTop(handle)
        user32.SetForegroundWindow(handle)
    except Exception:
        return False
    finally:
        if attached:
            try:
                user32.AttachThreadInput(ours, theirs, False)
            except Exception:
                pass
    return True


def activate(fragment: str) -> tuple[bool, str]:
    """Bring a named window to the front. (worked, title)."""
    handle, title = find(fragment)
    if not handle:
        return False, ""
    return _raise(handle), title


def _target() -> tuple[int, str]:
    """The window a command means: the one in front. (handle, title)."""
    handle = _foreground_handle()
    if not handle or _is_shell(handle):
        return 0, ""
    return handle, window_title()


def _owner(handle: int) -> str:
    """The exe that owns a window, lowercase and without ".exe"."""
    try:
        import ctypes
        import ctypes.wintypes

        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if not pid.value:
            return ""

        import psutil

        name = psutil.Process(pid.value).name().lower()
        return name[:-4] if name.endswith(".exe") else name
    except Exception:
        return ""


def console_handle() -> int:
    """The HWND of this process's own console window, or 0.

    GetConsoleWindow answers for the CALLING process, so this is not a guess
    about which window might be LAYA's console - it is the console LAYA is
    attached to, named by Windows itself.

    That exactness is the point. type_text used to protect the console by
    looking for this app's own name, "python" or "powershell" in the title, and a
    title is the one property of a window that the user controls: a Notepad
    file called "python notes.txt" was refused, while a console with its
    title changed was not. Both failures came from asking the wrong thing.

    Returns 0 when there is no console - a pythonw/GUI launch, where the
    process-name list in config.TYPE_BLOCKED_APPS is the remaining guard.
    """
    try:
        import ctypes

        return ctypes.windll.kernel32.GetConsoleWindow() or 0
    except Exception:
        return 0


def windows_of(names) -> list[tuple[int, str, str]]:
    """Every visible window owned by one of `names`, as (handle, title, exe).

    Ordered by the preference in `names` first and Z-order second, so the
    caller gets "the most recently fronted window of the most preferred app"
    without having to sort it out itself. Minimised windows come last within
    an app, matching find_by_process - one already on screen is cheaper to
    raise than one that has to be restored first.
    """
    wanted = [n.strip().lower() for n in names if n and n.strip()]
    if not wanted:
        return []

    rank = {name: n for n, name in enumerate(wanted)}
    found: list[tuple[int, str, str]] = []
    # _enumerate is already most-recently-fronted first, so enumerate() here
    # captures Z-order as a number that survives the sort below.
    for order, (handle, title) in enumerate(_enumerate()):
        exe = _owner(handle)
        if exe in rank:
            found.append((rank[exe], is_minimised(handle), order,
                          handle, title, exe))
    found.sort()
    return [(handle, title, exe) for _, _, _, handle, title, exe in found]


def find_by_process(exe: str) -> tuple[int, str]:
    """A visible window belonging to `exe`, or (0, "").

    By owning process rather than by title, because a title says nothing
    reliable: Notepad's window is called "Untitled - Notepad" until you type,
    then "*Untitled - Notepad", then the filename. The exe does not move.

    Prefers a window that is not minimised - raising one that is already on
    screen is instant, where an iconified one has to be restored first.
    """
    exe = (exe or "").strip().lower()
    if exe.endswith(".exe"):
        exe = exe[:-4]
    if not exe:
        return 0, ""

    hits = [(h, t) for h, t in _enumerate() if _owner(h) == exe]
    if not hits:
        return 0, ""
    hits.sort(key=lambda row: is_minimised(row[0]))
    return hits[0]


def activate_process(exe: str) -> tuple[bool, str]:
    """Raise an already-running app instead of starting another copy.

    This is what stops "notepad kholo" from leaving five empty Notepads
    behind. Said once it opens the app; said again it should bring you back
    to the one you already have, which is also what makes the next command
    land on the window you are looking at.
    """
    handle, title = find_by_process(exe)
    if not handle:
        return False, ""
    return _raise(handle), title


def _settled(check, want: bool, timeout: float = 1.2) -> bool:
    """Poll until `check()` is `want`. This is the verification step.

    Asking a window to minimise and assuming it did is how "minimize kaam
    nahi karta" stays invisible: ShowWindow returns before the window has
    moved, and it returns the same way when the window ignores it. Reading
    the flag back is the only honest answer.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if bool(check()) is want:
            return True
        time.sleep(0.06)
    return bool(check()) is want


def minimise() -> tuple[bool, str]:
    """Minimise the window in front, verified. (worked, title)."""
    global _last_minimised
    handle, title = _target()
    if not handle:
        return False, ""
    _show(handle, _SW_MINIMIZE)
    if _settled(lambda: is_minimised(handle), True):
        _last_minimised = handle
        return True, title
    return False, title


def maximise() -> tuple[bool, str]:
    """Maximise the window in front, verified. (worked, title)."""
    handle, title = _target()
    if not handle:
        return False, ""
    _show(handle, _SW_MAXIMIZE)
    return _settled(lambda: is_maximised(handle), True), title


def restore() -> tuple[bool, str]:
    """Bring back the window that was minimised, verified. (worked, title).

    Looks for the one Sparrow minimised itself first, because "minimize karo"
    then "restore window" is the sequence people actually say. Falls back to
    any minimised window when that one is gone or was never set - the user
    minimised it by hand, and refusing on a technicality is not helpful.
    """
    global _last_minimised

    handle = _last_minimised
    if not handle or not _exists(handle) or not is_minimised(handle):
        candidates = [h for h, _ in _enumerate() if is_minimised(h)]
        handle = candidates[0] if candidates else 0
    if not handle:
        return False, ""

    # Read before restoring, so the caller can name the window it brought
    # back even if the restore itself goes wrong. Through _title_of: a
    # minimised window belongs to a process that may be wedged just like
    # any other, and this is the one path that is reached while the user is
    # waiting to see something appear.
    title = _title_of(handle)

    _show(handle, _SW_RESTORE)
    worked = _settled(lambda: is_minimised(handle), False)
    if worked:
        try:
            import ctypes

            ctypes.windll.user32.SetForegroundWindow(handle)
        except Exception:
            pass
        if handle == _last_minimised:
            _last_minimised = 0
    return worked, title


def close(handle: int = 0) -> tuple[bool, str]:
    """Close a window, verified. (worked, title). Defaults to the one in front.

    WM_CLOSE to one handle rather than Alt+F4, and that is a safety fix as
    much as a reliability one: Alt+F4 with the desktop focused opens the
    Windows shutdown dialog. A message addressed to a window cannot.

    `handle` is for closing a window you NAMED rather than the one you are
    looking at - "close notepad" while the console is in front. Without it,
    close_app would have to raise the window first and then close whatever
    ended up in front, which is two chances to close the wrong thing.

    "Worked" means the window went away OR asked a question. The second half
    is for apps that prompt; Windows 11 Notepad, measured, does not. It
    answers WM_CLOSE by closing even with unsaved changes and no #32770
    dialog anywhere - and the text is not lost, because it keeps the unsaved
    buffer in its own session store and hands it back, still marked unsaved,
    when the file is reopened. So do not read a missing prompt as a missing
    save: see test_close_app.py, which measures exactly this.
    """
    if handle:
        title = _title_of(handle)
    else:
        handle, title = _target()
    if not handle:
        return False, ""
    try:
        import ctypes

        ctypes.windll.user32.PostMessageW(handle, _WM_CLOSE, 0, 0)
    except Exception:
        return False, title

    gone = _settled(lambda: not _exists(handle), True)
    if gone:
        return True, title
    # Still there: a save-or-discard prompt is the usual reason, and the
    # front window changing to a dialog is how you can tell.
    return window_title() != title, title
