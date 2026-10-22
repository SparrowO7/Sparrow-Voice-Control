"""The hands. Everything here actually opens something on Windows."""

from __future__ import annotations

import os
import subprocess
import urllib.parse
import webbrowser
import time
from dataclasses import dataclass
from pathlib import Path

import config
import search

# Imported at module level rather than only inside the functions that use
# them. This started as a fix for the hang where the second voice command
# printed its intent and stopped dead - the theory being that a lazy
# `import state` in a command thread could block on the interpreter's import
# lock while the speech worker imported pyttsx3/comtypes.
#
# That theory is WRONG and prove_import_lock.py is what disproved it: Python
# has had per-module import locks since 3.3, so importing state never waits
# on an unrelated import of pyttsx3. Measured, the contended case came back
# in 6ms. The hang has another cause and main.py's watchdog is what will
# name it.
#
# The imports stay up here anyway, on the smaller honest grounds that they
# are what the file needs and doing it once at import time beats re-entering
# the import machinery on every command. The `import state` lines still
# dotted through the functions below are cheap lookups now; they are left
# where they are because they document which function reaches for what.
#
# Safe to hoist: none of these four import actions, so there is no cycle.
import keys
import router
import screen
import state

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class Result:
    ok: bool
    say: str                 # short line spoken back to you
    detail: str = ""         # full path etc., printed not spoken
    options: list[Path] | None = None  # runner-ups, when the pick was ambiguous
    confirmation_required: bool = False


# ------------------------------------------------------------------ primitives
def _spawn(target: str) -> bool:
    try:
        process = subprocess.Popen(
            [str(target)],
            creationflags=_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _focus_process(process, Path(target).stem)
        return True
    except OSError:
        return False


def _in_front(stem: str, timeout: float = 3.0) -> bool:
    """Wait until `stem`'s window is the one in front. Verify, do not assume.

    Requirement 7, applied to the one place it was missing and mattered
    most. A launch that puts no window in front is not a launch that
    worked: the next command goes to whatever IS in front, which is how
    "type hello" ended up in Edge and "close window" closed Edge's Save As
    dialog. Both were reported as successes.

    Polls rather than sleeps, so the common case - the window arrives in
    200ms - costs 200ms, and tries raising it again each time round. A new
    process often cannot take focus on its first attempt.
    """
    deadline = time.perf_counter() + timeout
    stem = (stem or "").lower()
    if not stem:
        return False
    while time.perf_counter() < deadline:
        if state.process_name() == stem:
            return True
        handle, _ = state.find_by_process(stem)
        if handle and state._raise(handle):
            return True
        time.sleep(0.15)
    return state.process_name() == stem


def _focus_process(process: subprocess.Popen, stem: str = "") -> bool:
    """Best-effort focus of the newly launched app, without pyautogui.

    Two ways an app can arrive, and waiting the wrong way for the wrong one
    is a second of silence on every launch.

    Most apps keep the pid we started: Notepad's window shows up under it
    within a few polls. But Chrome, Edge and anything else with a launcher
    stub hand off to an existing or re-spawned process and the stub EXITS -
    so no window will ever carry our pid, and polling for one burns the full
    budget before giving up. That is where `open chrome` was losing ~1s, and
    on a voice remote a second of nothing is the difference between "it
    worked" and "did it hear me?".

    So the poll watches for the launcher dying as well as for a window, and
    when it dies we stop asking about pids and ask about the executable name
    instead - which is what state.activate_process already knows how to do.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        import ctypes.wintypes

        user32 = ctypes.windll.user32
        pid = process.pid
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND,
                            ctypes.wintypes.LPARAM)
        def callback(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            window_pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if window_pid.value == pid:
                found.append(hwnd)
                return False
            return True

        for _ in range(20):
            user32.EnumWindows(callback, 0)
            if found:
                # state._raise, not a bare SetForegroundWindow: Windows
                # refuses that for a process that is not already in front,
                # silently, which is exactly what left Notepad behind Edge.
                return state._raise(found[0])
            if process.poll() is not None:
                break  # launcher stub exited - its pid will never own a window
            time.sleep(0.05)
    except (AttributeError, OSError, ValueError):
        return False

    # The stub handed off. Find the real window by executable name instead.
    return _in_front(stem) if stem else False


def _spawn_with(target: str, arg: str) -> bool:
    """Launch a program with one argument. Used to hand a URL to a browser."""
    try:
        subprocess.Popen(
            [str(target), arg],
            creationflags=_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except OSError:
        return False


def _open_path(path: Path) -> bool:
    try:
        os.startfile(str(path))  # default app, same as double-click
        return True
    except OSError:
        return False


def _app_paths_lookup(name: str) -> Path | None:
    """The registry key Win+R uses, which is how 'chrome' resolves at all."""
    import winreg

    exe = name if name.lower().endswith(".exe") else f"{name}.exe"
    subkey = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, subkey) as key:
                value, _ = winreg.QueryValueEx(key, "")
                path = Path(value.strip('"'))
                if path.is_file():
                    return path
        except OSError:
            continue
    return None


def resolve_app(name: str) -> Path | str | None:
    """Find a launchable target, or None. Never launches anything.

    Resolving first matters: `start nosuchapp` pops a modal error dialog
    behind the scenes and reports success, so we must not guess.
    """
    if not name:
        return None
    if name.endswith(":"):
        return name  # protocol like ms-settings:

    from shutil import which

    found = which(name) or which(f"{name}.exe")
    if found:
        return Path(found)

    registry_hit = _app_paths_lookup(name)
    if registry_hit:
        return registry_hit

    for hit in search.find_executable(name):
        if hit.suffix.lower() in (".exe", ".lnk"):
            return hit
    return None


def reveal_in_explorer(path: Path) -> bool:
    """Open the containing folder with the file already selected."""
    try:
        subprocess.Popen(["explorer", "/select,", str(path)])
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ apps
def open_app(app: str | None, spoken_term: str) -> Result:
    name = app or spoken_term
    if not name:
        return Result(False, "Kaunsa app kholna hai?")

    target = resolve_app(name)
    if target is None:
        # No such program. "open food4all" is a project, not an app - and
        # saying "app nahi mila" when the folder is sitting right there is
        # a useless answer. Degrade to what this bot is actually for.
        #
        # This path used to be masked: find_executable would return a .lnk
        # out of Office's Recent folder and we would open a stale shortcut
        # instead. Excluding those made this fallback necessary.
        folder = open_folder(spoken_term or name)
        if folder.ok:
            return folder
        found = open_file(spoken_term or name)
        if found.ok:
            return found
        return Result(False, f"{name} naam ka app ya file nahi mila")

    label = spoken_term or name
    if isinstance(target, str):  # protocol
        return (
            Result(True, f"{label} khol raha hoon", detail=target)
            if _open_path(Path(target)) or _spawn(target)
            else Result(False, f"{label} khul nahi paya")
        )

    # Already running? Raise that window instead of starting another copy.
    # Saying "notepad kholo" five times used to leave five empty Notepads,
    # and then "type this" and "save" went to whichever one happened to be
    # in front - which is most of what "current state ka control nahi hai"
    # actually was. The app you asked for is the one you already have open.
    import state

    raised, open_title = state.activate_process(target.stem)
    if raised:
        return Result(True, f"{label} saamne le aaya",
                      detail=f"already open: {open_title}")

    # .lnk/.cmd/.bat need the shell to interpret them; CreateProcess can't.
    shell_types = (".lnk", ".cmd", ".bat", ".url", ".msc")
    ok = _open_path(target) if target.suffix.lower() in shell_types else (
        _spawn(str(target)) or _open_path(target)
    )
    if not ok:
        return Result(False, f"{label} khul nahi paya", detail=str(target))
    return Result(True, f"{label} khol raha hoon",
                  detail=_landed_in(target.stem, str(target)))


def _landed_in(stem: str, target: str) -> str:
    """Say which window the NEXT command will go to. Requirement 7.

    "open notepad" used to report success the moment the process started,
    and that was true and useless: Notepad had launched behind Edge, so
    "type hello this is LAYA" typed into Edge, "save" opened Edge's Save As
    and "close window" closed that dialog. Three commands that each looked
    separately broken, one cause, and nothing in the output pointed at it
    because the launch had said it worked.

    So the detail line now names the window that is actually in front. When
    that is not the app you asked for, it says so instead of staying quiet -
    an honest "it opened but is still behind something" is a thing you can
    act on, and a bare success is not.
    """
    if _in_front(stem):
        return target
    where = state.window_title() or state.process_name() or "kuch aur"
    return f"{target}\n          khula, par saamne nahi aaya - abhi {where[:44]}"


# ------------------------------------------------------------------ folders
def open_folder(term: str) -> Result:
    if not term:
        return Result(False, "Kaunsa folder?")

    alias = config.FOLDER_ALIASES.get(term)
    if alias:
        if str(alias).startswith("shell:"):
            try:
                subprocess.Popen(["explorer", str(alias)])
                return Result(True, f"{term} khol raha hoon", detail=str(alias))
            except OSError:
                return Result(False, f"{term} khul nahi paya")
        if alias.is_dir() and _open_path(alias):
            return Result(True, f"{term} khol raha hoon", detail=str(alias))

    hits = search.find_folder(term)
    if not hits:
        return Result(False, f"{term} naam ka folder nahi mila")

    best = hits[0]
    if _open_path(best):
        return Result(True, f"{best.name} folder khol raha hoon",
                      detail=str(best), options=hits[1:4])
    return Result(False, f"{best.name} khul nahi paya", detail=str(best))


# ------------------------------------------------------------------ files
def open_file(term: str, file_type: str = "any") -> Result:
    if not term:
        return Result(False, "Kya dhoondhna hai?")

    hits = search.find_files(term, file_type)
    if not hits and file_type != "any":
        hits = search.find_files(term, "any")  # type hint was too narrow
        file_type = "any"

    if not hits:
        return Result(False, f"{term} kuch mila nahi")

    best = hits[0]
    label = best.name
    if _open_path(best):
        noun = "" if file_type == "any" else f"{file_type} "
        return Result(True, f"{noun}{label} khol raha hoon",
                      detail=str(best), options=hits[1:4])
    return Result(False, f"{label} khul nahi paya", detail=str(best))


# ------------------------------------------------------------------ web
def _search_in_front_tab(url: str) -> bool:
    """Open `url` in the browser already in front. (worked)

    This is what makes "open chrome" then "search for X" land in the same
    place. Handing the URL to a new process instead meant a second Chrome
    window, which the existing one usually swallows anyway - and when it
    does not, you end up reading a different window from the one you are
    looking at.

    A new tab rather than the current one: the page you were on is yours,
    and Ctrl+T costs you nothing to undo.

    Two paths, and CDP is strongly preferred where it exists. Typing into
    the address bar opens a tab the protocol never hears about, which left
    browser's pinned tab pointing at the page before it: after a search,
    "click the second link" read the OLD page's links while the new one was
    on screen. The keystroke path therefore has to confess what it did by
    clearing that pin.
    """
    import keys

    # CDP path: returns the new tab's id, so state stays correct, and the
    # navigation is read back rather than assumed.
    try:
        import browser

        if browser.is_live() and "chrome" in keys.foreground_window().lower():
            if browser.open_tab(url):
                return True
            # Fall through - a failed CDP open should not stop the search.
            browser.unpin()
    except Exception:
        pass

    if not keys.press("ctrl+t"):
        return False
    time.sleep(0.6)

    try:
        import pyperclip

        pyperclip.copy(url)
    except Exception:
        return False

    pasted = keys.press("ctrl+v")
    time.sleep(0.2)
    if not pasted or not keys.press("enter"):
        return False

    # Whatever tab CDP thought it was in, it is not that one any more.
    try:
        import browser

        browser.unpin()
    except Exception:
        pass
    return True


def _same_browser_in_front(process: str, named: str | None) -> bool:
    """Is the browser already in front the one this command means?"""
    if not process:
        return False

    import state

    if not state.is_browser(process):
        return False
    if named is None:
        return True
    # "chrome kholo" while Chrome is the current window is a no-op, not a
    # reason to start a second copy of it.
    return process in str(named).lower()


def _open_url(url: str, named: str | None = None) -> tuple[bool, str]:
    """Put a URL on screen. (worked, how it got there).

    Tries the browser already in front first, because that is the one you are
    looking at - see _search_in_front_tab. Then the browser you named, then
    whatever Windows considers default. Shared by web_search and the YouTube
    flow so there is one answer to "where does a link open", not two.
    """
    import state

    front = state.front()
    if _same_browser_in_front(front["process"], named):
        if _search_in_front_tab(url):
            return True, f"in the open browser ({front['process']})"
        # Typing into it did not work, so fall through to a normal launch
        # rather than giving up with the browser sitting right there.

    if named:
        target = resolve_app(named)
        if target is not None and _spawn_with(str(target), url):
            return True, f"via {target}"

    try:
        webbrowser.open(url)
        return True, "via default browser"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _wait_for_title(fragment: str, timeout: float = 8.0) -> str:
    """Poll the front window title until it contains `fragment`.

    This is the verification step for anything that navigates. The old
    YouTube flow slept a fixed four seconds and then declared success, so a
    page that never loaded and a page that loaded fine reported identically.
    """
    import state

    fragment = fragment.lower()
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        last = state.window_title()
        if fragment in last.lower():
            return last
        time.sleep(0.25)
    return last


def web_search(term: str, browser: str | None = None) -> Result:
    """Google the term, in the browser you named if you named one.

    webbrowser.get("chrome") needs the browser registered and is unreliable
    on Windows, so the exe gets the URL as an argument instead. If that
    browser isn't installed we still search - in the default browser, which
    beats refusing.
    """
    if not term:
        return Result(False, "Kya search karna hai?")
    url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(term)

    worked, how = _open_url(url, browser)
    if not worked:
        return Result(False, f"{term} search nahi kar paya", detail=f"{url}\n          {how}")
    return Result(True, f"{term} search kar raha hoon",
                  detail=f"{url}\n          {how}")


# ------------------------------------------------------------------ screen
# Readable names for the log line and for optional spoken confirmation.
# Saying "right" out loud is useless; saying "next" is not.
_SPEC_LABELS = {
    "right": "next", "left": "back", "home": "first slide", "end": "last slide",
    "f5": "presentation chalu", "shift+f5": "yahan se present",
    "esc": "presentation band", "b": "screen kala", "w": "screen safed",
    "page_down": "neeche", "page_up": "upar", "f11": "full screen",
    "alt+tab": "window switch", "media_volume_up": "awaaz tez",
    "media_volume_down": "awaaz kam", "media_volume_mute": "mute",
    "media_play_pause": "play pause",
    # window management
    "win+down": "minimize", "win+up": "maximize",
    "alt+f4": "window band", "win+d": "desktop",
    "win+left": "snap left", "win+right": "snap right",
    # tabs
    "ctrl+t": "naya tab", "ctrl+w": "tab band",
    "ctrl+tab": "agla tab", "ctrl+shift+tab": "pichla tab",
    # clipboard
    "ctrl+c": "copy", "ctrl+v": "paste", "ctrl+x": "cut",
    "ctrl+a": "select all", "ctrl+z": "undo", "ctrl+y": "redo",
    # save/print/find
    "ctrl+s": "save", "ctrl+p": "print", "ctrl+f": "find",
    "ctrl+n": "naya window", "ctrl+r": "refresh",
    "ctrl+=": "zoom in", "ctrl+-": "zoom out",
    # system
    "win+l": "lock", "win+shift+s": "screenshot",
}

_CONFIRM_SPECS = {
    # Only what you cannot shrug off. Win+L ends the session, and Alt+F4 with
    # the desktop focused is the Windows shutdown dialog.
    #
    # Ctrl+W and Win+Shift+S used to be in here, and that was the whole
    # reason "close tab" looked broken: press_keys answered "haan bolo" and
    # waited, the follow-up "haan" was a word too short for the mic to catch,
    # and the command just evaporated. Closing a tab is ordinary. Asking
    # about it cost more than it saved.
    #
    # "close window" no longer arrives here at all - it is a window_action
    # now, which closes one window by handle and can never hit the shutdown
    # dialog the way a blind Alt+F4 can.
    "alt+f4", "win+l",
}

# The keys whose meaning depends on which program is in front. "go back" is
# an arrow key in a slide deck and Alt+Left in a browser, and the sentence is
# the same either way - so the front window has to decide.
_DIRECTION_SPECS = {"left", "right"}


def _resolve_spec(spec: str) -> tuple[str, str]:
    """(key to actually send, why). Browser navigation when one is in front."""
    if spec not in _DIRECTION_SPECS:
        return spec, ""

    import state

    browser_key = state.browser_command(spec)
    if browser_key:
        return browser_key, f"browser {spec}"
    return spec, ""


def requires_confirmation(intent) -> bool:
    """Return whether executing an action could surprise or disrupt the user."""
    return intent.name == "press_keys" and intent.term in _CONFIRM_SPECS


# What each window verb says back, and what it is called when it fails.
_WINDOW_LABELS = {
    "minimize": ("minimize kar diya", "minimize"),
    "maximize": ("maximize kar diya", "maximize"),
    "restore": ("wapas le aaya", "restore"),
    "close": ("window band kar diya", "band"),
}


def window_action(verb: str) -> Result:
    """Minimise, maximise, restore or close a window - by handle, and verified.

    Keystrokes cannot do this job. Win+Down worked, but Win+Up cannot restore
    what Win+Down minimised: a minimised window has no focus, so the key goes
    to whatever is behind it. And Alt+F4 is worse than unreliable - with the
    desktop focused it opens the shutdown dialog.

    Every verb here reads the window's own flags back afterwards, so "nahi
    kar paya" means the window really did not move, rather than "the call
    returned without raising".
    """
    import state

    handlers = {
        "minimize": state.minimise,
        "maximize": state.maximise,
        "restore": state.restore,
        "close": state.close,
    }
    handler = handlers.get(verb)
    if handler is None:
        return Result(False, "Window ka kya karna hai?", detail=f"unknown verb {verb!r}")

    said, short = _WINDOW_LABELS[verb]
    worked, title = handler()

    if worked:
        return Result(True, said, detail=f"{short}: {title or '(no title)'}")

    if not title:
        if verb == "restore":
            # Nothing is minimised, so there is nothing to bring back.
            return Result(False, "Koi minimized window nahi hai",
                          detail="restore: no minimised window found")
        return Result(False, "Koi window front me nahi hai",
                      detail=f"{short}: desktop or taskbar is focused")
    return Result(False, f"{short} nahi kar paya",
                  detail=f"{short}: {title} did not change state")


def close_app(app: str) -> Result:
    """Close the windows of a NAMED app, verified. Launches nothing, ever.

    The command this exists for is "close notepad", and what it used to do
    was the opposite:

        heard   "Close Chrome."
        intent  open_app  type=any  term="close chrome"  via keywords
        open    close chrome saamne le aaya

    It brought to the front the window you had just asked it to close,
    because naming an app was read as wanting it launched and the verb in
    front of the name was never consulted. Router-side that is fixed by
    asking about the verb first; this is the other half - an action that
    actually closes something.

    Three things it will not do. It does not launch: an app that is not open
    has nothing to close, and saying so is the whole answer. It does not
    close LAYA's own console, which would end the session that was asked to
    do it. And it does not report success without looking - state.close
    reads the window back, and what is still on screen afterwards is
    re-enumerated here, so "band kar diya" means the windows are gone.
    """
    name = (app or "").strip().lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if not name:
        return Result(False, "Kya band karna hai?")

    windows = state.windows_of([name])
    if not windows:
        return Result(False, f"{name} khula hi nahi hai",
                      detail="nothing to close - no visible window owned by it")

    console = state.console_handle()
    if console and any(handle == console for handle, _, _ in windows):
        windows = [row for row in windows if row[0] != console]
        if not windows:
            return Result(False, "Wo LAYA ka apna console hai - band nahi karunga",
                          detail="refused: closing it would end this session")

    asked, dialogs = [], []
    for handle, title, _exe in windows:
        worked, got = state.close(handle)
        label = got or title or "(no title)"
        if worked and state._exists(handle):
            # state.close counts a save-or-discard prompt as working, because
            # the app did respond - but the window is still there and the
            # close is now waiting on you, not on us. Say which.
            dialogs.append(label)
        elif worked:
            asked.append(label)

    left = state.windows_of([name])
    if console:
        left = [row for row in left if row[0] != console]

    if dialogs:
        return Result(True, f"{name} poochh raha hai - save karna hai?",
                      detail=f"close sent; prompt open: {dialogs[0][:44]}")
    if not left:
        shut = ", ".join(asked)[:60] or name
        return Result(True, f"{name} band kar diya", detail=f"closed: {shut}")
    return Result(False, f"{name} band nahi hua",
                  detail=f"{len(left)} window(s) still open: "
                         f"{left[0][1][:44]!r}")


def press_keys(spec: str) -> Result:
    """Send a keystroke to the window in front. Opens nothing."""
    import keys

    if not spec:
        return Result(False, "Kaunsa control?")

    resolved, why = _resolve_spec(spec)

    label = _SPEC_LABELS.get(spec, spec)
    if resolved != spec:
        # Say the browser meaning, not the arrow key: "back" is what you
        # asked for, "alt+left" is only how this program spells it.
        label = _SPEC_LABELS.get(resolved, f"{label} ({why})")
    if spec in _CONFIRM_SPECS:
        return Result(
            False,
            f"{label} karne ke liye haan bolo",
            detail=f"confirmation required for {spec}",
            confirmation_required=True,
        )
    where = keys.foreground_window()
    if not keys.press(resolved):
        return Result(False, f"{label} bhej nahi paya", detail=f"{resolved}  ->  {where}")

    landed = _after_keys(where)
    if landed.get("dialog"):
        # A dialog means the shortcut asked a question, and the action has
        # NOT happened yet. "save" is the one that made this matter: ctrl+s
        # on an unsaved file opens Save As and waits for a filename, and
        # saying "save" as though it were done is how "save kaam nahi
        # karta" looked from the outside - the reply said yes, the file was
        # never written, and the next command went to the dialog.
        return Result(True, f"{landed['title'][:40]} khula hai - filename bolo",
                      detail=f"{resolved}  ->  dialog: {landed['title']}")
    detail = f"{resolved}  ->  {where}"
    if landed.get("title") and landed["title"] != where:
        detail += f"\n          ab: {landed['title']}"
    return Result(True, label, detail=detail)


def _after_keys(before: str, settle: float = 0.45) -> dict:
    """What the keystroke actually did to the screen. Requirement 7.

    press_keys verified nothing before this: it sent the keys, and if the
    send succeeded it said the action succeeded. Those are different
    claims, and the gap between them is where "save" lived.

    A standard Win32 dialog is class #32770 - Save As, Open, Print,
    Properties, all of them, in every app and every language. Checking the
    class rather than the title is what keeps this out of patch territory:
    no "Save As" string to match, so it works the same on a Hindi Windows
    and on a dialog nobody anticipated.
    """
    time.sleep(settle)
    try:
        handle = state._foreground_handle()
        if not handle:
            return {}
        return {"title": state.window_title(),
                "dialog": state._class_name(handle) == "#32770"}
    except Exception:
        return {}


# ------------------------------------------------------------------ type text
# The exe of the window the last dictated line actually landed in. Used only
# to break a tie between several open editors - see _pick_target.
_last_typed_into = ""


def _refusal(handle: int, exe: str) -> str:
    """Why this window must not be typed into, or "" if it may be.

    Two checks, and the first one is exact. GetConsoleWindow names LAYA's
    own console outright, so there is no guessing about whether the window
    in front is the one running this code.

    The second is a process list, for a terminal that hosts the process
    without owning a console window of its own - a Windows Terminal tab, an
    IDE's integrated shell. Both ask about the process. Neither asks about
    the title, which is what the old check did: it looked for this app's
    own name,
    "python" or "powershell" in the title, so a Notepad file called "python
    notes.txt" was refused and a console with a changed title was not.
    """
    if handle and handle == state.console_handle():
        return "LAYA ka apna console"
    if exe and exe in config.TYPE_BLOCKED_APPS:
        return f"{exe} ek shell hai"
    return ""


def _front_window() -> tuple[int, str, str]:
    """(handle, title, exe) for the window in front. Empty strings if unread."""
    try:
        handle = state._foreground_handle()
        if not handle:
            return 0, "", ""
        return handle, state.window_title(), state._owner(handle)
    except Exception:
        return 0, "", ""


def _focus_for_typing(handle: int, exe: str) -> tuple[bool, str]:
    """Raise `handle` and confirm it is really in front. (ok, what_is_front).

    Requirement 8, and it is the difference between this working and this
    being dangerous. Raising a window can fail silently - that is the whole
    story of state._raise - and a dictated sentence sent to whatever stayed
    in front is the exact thing the console guard exists to prevent. So the
    foreground is read BACK and the exe compared, and a mismatch refuses
    rather than types.
    """
    try:
        state._raise(handle)
    except Exception:
        pass

    now_handle, now_title, now_exe = _front_window()
    if now_handle == handle and not _refusal(now_handle, now_exe):
        return True, now_title
    if now_exe == exe and not _refusal(now_handle, now_exe):
        # Same app, different window of it - Notepad opening a second
        # untitled document, for instance. The app is what was asked for.
        return True, now_title
    return False, now_title or now_exe or "(kuch nahi)"


def _pick_target(text: str) -> tuple[int, str, str, Result | None]:
    """Find a window to type into when the one in front cannot be used.

    (handle, title, exe, refusal_result). Only the editors in
    config.TYPE_AUTO_TARGETS are considered: a browser is fine to type into
    when you chose it yourself by looking at it, but picking one unprompted
    can put a sentence into a page, an address bar or a keyboard shortcut.

    Nothing is launched here. "Notepad is already open" is the case this
    exists for, and opening a second one would leave the first - the one you
    were looking at - untouched.
    """
    candidates = [(h, t, e) for h, t, e in
                  state.windows_of(config.TYPE_AUTO_TARGETS)
                  if not _refusal(h, e)]

    if not candidates:
        return 0, "", "", Result(
            False, "Notepad ya target app pehle kholo; console mein nahi likhunga",
            detail=f"no typeable window open; front is protected\n"
                   f"          wanted one of: {', '.join(config.TYPE_AUTO_TARGETS[:4])}...")

    apps = {exe for _, _, exe in candidates}
    if len(apps) > 1:
        # Several different editors are open and the sentence named none of
        # them. The last one a dictated line went into is a real answer;
        # anything else would be a guess, and this is the one place a guess
        # puts text somewhere you were not looking.
        if _last_typed_into in apps:
            for handle, title, exe in candidates:
                if exe == _last_typed_into:
                    return handle, title, exe, None
        names = ", ".join(sorted(apps))
        return 0, "", "", Result(
            False, f"Kahan likhna hai - {names}?",
            detail=f'say "type on <app> {text[:24]}" to choose\n'
                   f"          open editors: {names}")

    handle, title, exe = candidates[0]
    return handle, title, exe, None


def _send_text(text: str) -> None:
    """Put `text` into the focused window, exactly, via the clipboard.

    It used to type character by character with pyautogui.write(interval=
    0.02), and that silently drops keys. Measured, 5 trials of a 42-character
    line into Notepad:

        write  interval=0.02, no settle   3/5 exact
        write  interval=0.02, 0.25s       4/5
        write  interval=0.05, 0.25s       5/5
        paste               , 0.25s       5/5

    The failures were a single missing letter in the middle - 'laya arget
    test', 'laya target est'. That is the worst shape a dictation bug can
    have: it still says "likh diya", the sentence still looks like a
    sentence, and you find the damage later.

    A paste is one event regardless of length, so it cannot half-arrive, and
    it does not get slower as the line gets longer the way interval=0.05
    does. The cost is the clipboard, which is why the old contents are put
    back afterwards - best effort, and text only: if you had an image copied,
    pyperclip cannot carry it and this will clear it.
    """
    import pyautogui

    try:
        import pyperclip
    except Exception:
        # No clipboard. Type it, slowly enough to be reliable per the table
        # above, rather than fail outright.
        pyautogui.write(text, interval=0.05)
        return

    try:
        previous = pyperclip.paste()
    except Exception:
        previous = None

    pyperclip.copy(text)
    time.sleep(0.12)          # let the clipboard settle before Ctrl+V reads it
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.25)          # and before the clipboard is taken back again

    if previous is not None:
        try:
            pyperclip.copy(previous)
        except Exception:
            pass


def type_text(text: str, target: str = "") -> Result:
    """Type a dictated line into a named window, or into a safe one in front.

    What this used to do was check the front window's title for this app's
    own name,
    "python" or "powershell" and give up if it matched. The refusal was
    right and stays - a dictated sentence at a shell prompt is a command
    nobody asked to run - but giving up was not the only option available:

        intent  type_text
        fail    Notepad ya target app pehle kholo; console mein nahi likhunga
        blocked foreground: Administrator: Sparrow - hold Ctrl+Alt and speak

    Notepad was open the whole time. It was simply behind the console, which
    is where it always is right after you speak - you were looking at LAYA's
    window to talk to LAYA. So the common case was the failing one.

    It now goes and finds that window, raises it, confirms it arrived, and
    types. What it does not do is launch anything: a second Notepad would
    leave the first one, the one with your text in it, untouched.
    """
    if not text:
        return Result(False, "Kya likhna hai?")

    handle, title, exe = _front_window()
    blocked = _refusal(handle, exe)

    if target:
        # You named the window. Find that one, and only that one.
        wanted = [(h, t, e) for h, t, e in state.windows_of([target])
                  if not _refusal(h, e)]
        if not wanted:
            if state.windows_of([target]):
                return Result(False, f"{target} mein nahi likh sakta",
                              detail=f"{target} is in config.TYPE_BLOCKED_APPS")
            return Result(False, f"{target} khula nahi hai - pehle kholo",
                          detail=f"no visible window owned by {target}")
        handle, title, exe = wanted[0]
    elif blocked:
        handle, title, exe, refused = _pick_target(text)
        if refused is not None:
            return Result(refused.ok, refused.say,
                          detail=f"{refused.detail}\n          front: {blocked}"
                                 f" ({title[:40] or exe})")
    elif not handle:
        return Result(False, "Koi window front me nahi hai",
                      detail="foreground is the desktop or taskbar")

    # Whether it was named, found, or already in front, the window has to be
    # confirmed in front before a single key is sent. Requirement 8.
    landed, now = _focus_for_typing(handle, exe)
    if not landed:
        return Result(False, f"{exe or 'window'} saamne nahi aa paya - likha nahi",
                      detail=f"wanted {exe} ({title[:38]}), front is {now[:38]}")

    try:
        # The window is in front, but "in front" and "ready for input" are
        # not the same instant - measured, sending with no pause here cost
        # 2 of 5 lines a character. state._raise's own settle is not enough
        # because it watches the foreground, not the input queue.
        time.sleep(0.25)

        # Re-check, because that pause is a hole. Focus was verified before
        # the sleep and anything on the desktop may take it during - measured
        # live: a browser came forward mid-test, the text went to it, and this
        # function still answered 'likh diya'. Claiming a dictated line
        # landed somewhere it did not is the worst failure available here,
        # because the text is now in a window nobody was looking at.
        still, where_now, still_exe = _front_window()
        if still != handle and still_exe != exe:
            return Result(False, f"focus chala gaya - {text[:20]!r} likha nahi",
                          detail=f"wanted {exe}, front became {where_now[:40]!r}"
                                 f"\n          nothing was sent")
        _send_text(text)
    except Exception as exc:
        return Result(False, f"likh nahi paya: {exc}")

    # And verify afterwards. The paste is one event, so if focus survived to
    # here it got there; what this catches is focus moving during the paste.
    after = _front_window()
    if after[0] != handle and state._owner(after[0]) != exe:
        return Result(False, f'"{text[:24]}" bheja par window badal gayi',
                      detail=f"sent to {exe}, front is now {after[1][:40]!r}"
                             f"\n          check both windows")

    global _last_typed_into
    _last_typed_into = exe
    return Result(True, f'likh diya: "{text}"', detail=f"in: {now or title}")


# ------------------------------------------------------------------ youtube
# _is_chrome_open() and _bring_chrome_to_front() used to live here. The
# second one "activated Chrome" with a blind Alt+Tab, which switches to
# whichever window happens to be next - not to Chrome. Nothing calls them
# now: state.front() answers what is in front, and state.activate(name)
# raises a window by its handle instead of guessing.

def youtube_channel(channel: str) -> Result:
    """Put YouTube's results for `channel` on screen, and stop there.

    This used to also try to play the first video, and the way it tried is
    what made it famous: on a results page the first Tab-focusable thing is
    the YouTube logo, which links to the homepage. So "press Tab, press
    Enter" to reach the filter dropdown actually clicked Home and threw the
    search away - the exact "search karta hai, phir wapas home pe chala jata
    hai" complaint. After that it clicked the fixed coordinate (400, 300) and
    returned success unconditionally, whatever was or was not there.

    All three of those are gone. Searching is one action; playing a result is
    the next command, and it goes through click_nth, which reads the page
    instead of guessing where the results are. The search is verified by
    waiting for the page title to actually say YouTube rather than sleeping
    and hoping.
    """
    if not channel:
        return Result(False, "Channel ka naam batao")

    url = config.YOUTUBE_SEARCH_URL + urllib.parse.quote_plus(channel)
    worked, how = _open_url(url, "chrome")
    if not worked:
        return Result(False, "YouTube khol nahi paya", detail=f"{url}\n          {how}")

    title = _wait_for_title("youtube")
    if "youtube" not in title.lower():
        return Result(False, f"{channel} search bheja, par page load nahi hua",
                      detail=f"{url}\n          {how}\n"
                             f"          title stayed: {title!r}")

    return Result(
        True,
        f"YouTube pe {channel} ke results khol diye",
        detail=f"{url}\n          {how}\n          {title}",
    )


# ------------------------------------------------------------------ clicking
# Your own browser is the default, deliberately. browser.py is more precise
# - it asks the page for its links instead of reading an accessibility tree
# - but Chrome 136+ refuses the debug port on your normal profile, so the
# Chrome it drives is a blank one with none of your logins. "Search for X,
# now click the second link" in a browser you are not signed in to is the
# wrong answer, so CDP is used only when that Chrome is already the window
# in front of you.
def _use_cdp() -> bool:
    try:
        import browser
        import keys
    except ImportError:
        return False

    if not browser.is_live():
        return False
    # The debuggable Chrome has to be the window you are looking at. Acting
    # on a background window you forgot about is worse than not acting.
    try:
        front = keys.foreground_window().lower()
    except Exception:
        return False
    return "chrome" in front


def _clicked(outcome: dict | None, asked: str, navigates: bool = False) -> Result:
    """Turn either backend's verdict into something worth hearing.

    `navigates` says whether the page was expected to go somewhere. It is
    true for "click the second link" - a search result that does not move
    the page did not work, whatever .click() returned - and false for
    "click settings", where opening a menu without changing the URL is the
    correct outcome.

    A missing "landed" key means the backend could not tell, and that is
    reported as unverified rather than as failure. Saying "nahi hua" about
    something that did happen is its own bug.

    Both backends can answer now. CDP compares the url; UIA compares the
    window title, which moves on navigation even though the url is out of
    reach - that is how "harsh tiwari portfolio - Google Search - Google
    Chrome" becoming "हर्ष तिवारी का पोर्टफोलियो - Google Chrome" counts as proof.
    """
    if outcome is None:
        return Result(False, "Screen padh nahi paya",
                      detail="no accessible window / CDP target")
    if outcome.get("ok"):
        title = (outcome.get("title") or "").strip()
        said = title[:60] if title else asked
        landed = outcome.get("landed")
        where = outcome.get("url") or ""

        if navigates and landed is False:
            stayed = where or outcome.get("was") or "same page"
            return Result(False, f"{said} pe click kiya par page khula nahi",
                          detail=f"clicked {title!r} - stayed on {stayed}")

        detail = f"clicked: {title}"
        if where:
            detail += f"\n          now at: {where}"
        elif landed:
            detail += "\n          verified: window title changed"
        if landed is None:
            detail += "\n          (unverified - could not read the page before/after)"
        return Result(True, f"{said} khol raha hoon", detail=detail)

    count = outcome.get("count")
    if count is not None:
        return Result(False, f"Utne link nahi hain, sirf {count} dikh rahe hain",
                      detail=f"asked {asked}, found {count}")
    return Result(False, f"{asked} naam ka kuch nahi mila",
                  detail=f"no match for {asked!r}")


def click_nth(position_text: str) -> Result:
    """Click the nth link in whatever window is in front."""
    if not position_text:
        return Result(False, "Kaunse link pe? Pehla, doosra?")
    try:
        position = int(position_text)
    except ValueError:
        return Result(False, "Kaunse link pe? Pehla, doosra?")

    asked = "aakhri link" if position < 0 else f"link {position}"

    if _use_cdp():
        import browser
        return _clicked(browser.click_nth(position), asked, navigates=True)

    import screen
    if not screen.available():
        return Result(False, "Screen padhne ka module nahi hai",
                      detail="pip install uiautomation")
    return _clicked(screen.click_nth(position), asked)


def click_named(name: str) -> Result:
    """Click the link or button whose text matches what you said."""
    if not name:
        return Result(False, "Kis cheez pe click karna hai?")

    if _use_cdp():
        import browser
        return _clicked(browser.click_named(name), name)

    import screen
    if not screen.available():
        return Result(False, "Screen padhne ka module nahi hai",
                      detail="pip install uiautomation")
    return _clicked(screen.click_named(name), name)


# ------------------------------------------------------------------ dispatch
def preview(intent, raw_text: str = "") -> Result:
    """Resolve the target without opening it. Used by --dry."""
    import router

    if intent.name == "press_keys":
        import keys

        # An empty spec is the router telling us it heard a key combo it
        # could not send. Mirror what press_keys() does rather than
        # printing "would press ()".
        if not intent.term:
            return Result(False, "Kaunsa control?")
        label = _SPEC_LABELS.get(intent.term, intent.term)
        return Result(True, f"would press {intent.term} ({label}) "
                            f"in: {keys.foreground_window()}")

    if intent.name == "window_action":
        import state

        front = state.front()
        where = front["title"] or "(nothing in front)"
        return Result(True, f"would {intent.term}: {where}")

    if intent.name == "open_app":
        app = router.match_app(raw_text or intent.term) or router.match_app(intent.term)
        target = resolve_app(app or intent.term)
        if target:
            return Result(True, f"would launch: {target}")

        # Same degradation as open_app: no program, so show what it would
        # actually land on instead of a bare "no app matched".
        hits = search.find_folder(intent.term)
        if hits:
            return Result(True, f"would open folder: {hits[0]}", options=hits[1:4])
        hits = search.find_files(intent.term)
        if hits:
            return Result(True, f"would open: {hits[0]}", options=hits[1:4])
        return Result(False, f"no app, folder or file matched '{app or intent.term}'")

    if intent.name == "open_folder":
        alias = config.FOLDER_ALIASES.get(intent.term)
        if alias:
            return Result(True, f"would open folder: {alias}")
        hits = search.find_folder(intent.term)
        return (
            Result(True, f"would open folder: {hits[0]}", options=hits[1:4])
            if hits
            else Result(False, f"no folder matched '{intent.term}'")
        )

    if intent.name == "open_file":
        hits = search.find_files(intent.term, intent.file_type)
        if not hits and intent.file_type != "any":
            hits = search.find_files(intent.term, "any")
        return (
            Result(True, f"would open: {hits[0]}", options=hits[1:4])
            if hits
            else Result(False, f"no file matched '{intent.term}'")
        )

    if intent.name == "web_search":
        if not intent.term:
            return Result(False, "Kya search karna hai?")
        where = router.match_browser(raw_text or intent.term)
        via = f" in {where}" if where else ""
        return Result(True, f"would search the web for: {intent.term}{via}")
    if intent.name == "type_text":
        where = getattr(intent, "target", "") or "the window in front"
        return Result(True, f'would type: "{intent.term}"  ->  {where}')
    if intent.name == "close_app":
        open_now = state.windows_of([intent.term])
        if not open_now:
            return Result(False, f"{intent.term} khula hi nahi hai")
        return Result(True, f"would close {len(open_now)} {intent.term} window(s)",
                      detail=open_now[0][1][:50])
    if intent.name == "click_nth":
        if not intent.term:
            return Result(False, "Kaunse link pe? Pehla, doosra?")
        where = "debuggable chrome (CDP)" if _use_cdp() else "front window (UIA)"
        return Result(True, f"would click link {intent.term} via {where}")
    if intent.name == "click_named":
        where = "debuggable chrome (CDP)" if _use_cdp() else "front window (UIA)"
        return Result(True, f'would click "{intent.term}" via {where}')
    if intent.name == "youtube_channel":
        return Result(True, f"would open YouTube results for '{intent.term}'")
    return Result(False, "would do nothing")


def run(intent, raw_text: str = "") -> Result:
    """Take a router.Intent and do the thing."""
    if intent.name == "press_keys":
        return press_keys(intent.term)
    if intent.name == "window_action":
        return window_action(intent.term)
    if intent.name == "close_app":
        return close_app(intent.term)
    if intent.name == "open_app":
        import router

        # Match against the full sentence - the alias may be a stopword-ish
        # word that extract_term dropped (e.g. "files", "settings").
        app = router.match_app(raw_text or intent.term) or router.match_app(intent.term)
        return open_app(app, intent.term)
    if intent.name == "open_folder":
        return open_folder(intent.term)
    if intent.name == "open_file":
        return open_file(intent.term, intent.file_type)
    if intent.name == "web_search":
        import router

        # The browser lives in the sentence, not in the term - route()
        # strips it out so it never ends up inside the search query.
        return web_search(intent.term, router.match_browser(raw_text))
    if intent.name == "type_text":
        # getattr, not intent.target: action_schema builds intent-shaped
        # objects too, and an older one without the field should still type
        # into the front window rather than raise.
        return type_text(intent.term, getattr(intent, "target", "") or "")
    if intent.name == "click_nth":
        return click_nth(intent.term)
    if intent.name == "click_named":
        return click_named(intent.term)
    if intent.name == "youtube_channel":
        return youtube_channel(intent.term)

    # Nothing confident came back. Doing nothing and asking is the whole
    # point: the old behaviour here was a disk search, which is how a
    # half-heard sentence used to launch a random file.
    if intent.term:
        return Result(False, f'"{intent.term}" ka kya karna hai? Kholna, '
                             f"search karna, ya click?",
                      detail=f"unresolved: {intent.name} term={intent.term!r}")
    return Result(False, "Samajh nahi aaya, phir se bolo",
                  detail=f"unresolved: {intent.name}")
