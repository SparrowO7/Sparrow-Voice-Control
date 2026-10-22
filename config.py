"""Tweak everything here. No other file needs editing for day-to-day changes."""

import os
from pathlib import Path

HOME = Path.home()
PROJECT = Path(__file__).parent
CACHE = PROJECT / "cache"

# ---------------------------------------------------------------- devices
# "auto" picks CUDA when the dGPU is awake, otherwise CPU. The 4050 stays
# unused while the MUX switch is off, so CPU + int8 is the normal path.
DEVICE = os.environ.get("SPARROW_DEVICE", "auto")

# ---------------------------------------------------------------- hotkey
# Hold both, speak, release. Recording starts on the second key down.
# ---------------------------------------------------------------- trigger
# "both" -> earbud tap AND keyboard hold, dono ek saath. Default.
# "tap"  -> sirf earbud tap. Across the room ke liye.
# "hold" -> sirf Ctrl+Alt hold on laptop keyboard.
TRIGGER = os.environ.get("SPARROW_TRIGGER", "both")

# Earbuds send a media key on a single tap. play/pause is near-universal;
# some send next/previous instead - run `python keytest.py` to see yours.
TAP_KEY = os.environ.get("SPARROW_TAP_KEY", "media_play_pause")

# Try to stop the tap from also reaching Spotify. Not all setups allow it.
TAP_SUPPRESS = True

# Tap recording stops by itself. It waits this long for you to start, then
# ends once you have been quiet for TAP_SILENCE_TAIL.
TAP_MAX_SECONDS = 6.0
TAP_LEAD_IN = 1.5          # grace period before silence can end the clip
TAP_SILENCE_TAIL = 0.7     # quiet for this long = you finished talking

HOTKEY = {"ctrl", "alt"}
MAX_RECORD_SECONDS = 15
SAMPLE_RATE = 16000

# None = Windows' default input device. Set to a number or a name fragment
# ("earbuds", "airdopes", "hands-free") when you speak into something other
# than the laptop mic. Run `python mics.py` to see the list.
MIC_DEVICE = os.environ.get("SPARROW_MIC") or None

# Below this loudness the clip is treated as silence and never sent to
# Whisper. Needed because Whisper answers a silent clip by parroting
# WHISPER_PROMPT back at you, which then routes as a real command.
SILENCE_RMS = 0.004

# ---------------------------------------------------------------- speech
# "small" handles Hinglish; "base" is ~2x faster but drops Hindi words.
WHISPER_MODEL = os.environ.get("SPARROW_WHISPER", "small")
# Forced to English on purpose: it transliterates Hinglish into Latin script
# ("food4all ka doc kholo") instead of Devanagari, which is what STOPWORDS
# below expect. Set to None if you want true Hindi output.
WHISPER_LANGUAGE = "en"

# Beam width. 1 is greedy, and measured, greedy is right - which is not what
# I expected when raising it. measure_stt.py, 12 synthesised command clips:
#
#   10/12  3180 ms/clip   beam=1, old launch-only prompt
#   11/12  3719 ms/clip   beam=1, derived vocabulary     <- this one
#   11/12  4213 ms/clip   beam=3, derived vocabulary
#   11/12  4092 ms/clip   beam=5, derived vocabulary
#
# The vocabulary buys the whole accuracy win and the beam buys nothing, for
# another ~400 ms on every command. The clip it recovered is one of the two
# that actually failed in a live session: "search for Harsh Tiwari portfolio"
# came back "Harsh Dewar reportfolio" without the vocabulary and correctly
# with it. Raise this with SPARROW_BEAM=5 if you want to re-test on your own
# voice - a real accent may answer differently to a synthesised one.
WHISPER_BEAM_SIZE = int(os.environ.get("SPARROW_BEAM", "1"))

# How many spoken clips to keep on disk, newest first. 0 turns it off.
#
# This exists because STT was the one subsystem with no evidence trail. Every
# other bug in this program got fixed by measuring it - the window hang by a
# stack-dumping watchdog, the focus bug by reading window state back, the
# wrong link by dumping the UIA tree. A mishear left nothing behind: the
# audio was discarded and all that survived was the wrong text, so "save
# ko see you likh raha hai" could only be guessed at.
#
# A clip is a few hundred KB and twenty of them is a few megabytes, which is
# nothing against being able to replay the exact audio that failed. See
# replay_clip.py.
KEEP_CLIPS = int(os.environ.get("SPARROW_KEEP_CLIPS", "20"))
CLIPS = CACHE / "clips"

# Whisper guesses words it has heard before, so showing it the vocabulary up
# front is the cheapest accuracy win available. Without this it writes
# "colo" for "kholo" and "food for all" for "food4all".
#
# WHISPER_PROMPT and WHISPER_HOTWORDS are both defined at the BOTTOM of this
# file, because the hotword list is derived from the command tables and those
# are declared below. The hand-written prompt that used to live here is why:
# it only ever listed launch phrases, and every action command added to the
# tables afterwards was missing from it.

SPEAK_REPLIES = True
TTS_RATE = 190

# Whisper still mishears some of these. Word-level repairs, applied to the
# transcript before routing. Add whatever it gets wrong on your voice.
SPOKEN_FIXES = {
    "colo": "kholo", "kolo": "kholo", "cholo": "kholo", "kulo": "kholo",
    "khulo": "kholo", "kholo.": "kholo", "colour": "kholo",
    "diko": "dikhao", "dikao": "dikhao", "dika": "dikhao",
    "mara": "mera", "meera": "mera", "mere": "mera",
    "karu": "karo", "corro": "karo", "kuro": "karo",
    "dundo": "dhundo", "dhundho": "dhundo",
    "nikalo.": "nikalo", "ka.": "ka",
    # Whisper commonly reduces "notepad" to a phonetic-looking app name.
    "ipad": "notepad", "i-pad": "notepad", "modepad": "notepad",
    "mode-pad": "notepad", "modpad": "notepad", "rootpad": "notepad",
    # British spellings. KEY_COMMANDS is keyed on the American one, and
    # "minimise" was falling through to a file search.
    "minimise": "minimize", "maximise": "maximize",
    "minimises": "minimize", "normalise": "normalize",
}

# Spoken form -> what the file is actually called. Digits in a name are the
# usual culprit: "food4all" comes out of your mouth as "food four all", and
# Whisper writes "food for all". Longest phrase wins, so order is irrelevant.
NAME_ALIASES = {
    "food for all": "food4all",
    "food four all": "food4all",
    "food 4 all": "food4all",
    "foodforall": "food4all",
    "food for all's": "food4all",
    "i pad": "notepad",
    "mode pad": "notepad",
    "mod pad": "notepad",
    "root pad": "notepad",
}

# ---------------------------------------------------------------- youtube
# YouTube automation: opens Chrome, searches a channel, clicks 1st video.
# Delays give YouTube time to load between steps.
YOUTUBE_URL = "https://www.youtube.com"
YOUTUBE_SEARCH_URL = "https://www.youtube.com/results?search_query="
YOUTUBE_HOME = "https://www.youtube.com"
CHROME_TIMEOUT = 8          # seconds to wait for Chrome to open
PAGE_LOAD_WAIT = 4          # seconds after URL navigation
SEARCH_WAIT = 3             # seconds after typing search
VIDEO_CLICK_WAIT = 5        # seconds for video page to load

# Words that mean "YouTube" in a sentence - stripped when building the search term
YOUTUBE_WORDS = {
    "youtube", "yt", "yout", "tuub", "tuube",
}

# ---------------------------------------------------------------- laya
LAYA_CHECKPOINT = "laya-multilingual"
MIN_CONFIDENCE = 0.45

# ---------------------------------------------------------------- keystrokes
# Presentation and media control: these send a key to whatever window is in
# front, instead of opening something. Matched against the WHOLE sentence
# (minus filler), never a substring - otherwise "desktop pe wapas jao" would
# trip the "wapas" entry and fire a Left arrow instead of opening a folder.
KEY_COMMANDS = {
    # --- slides, forward ---
    "next slide": "right", "next": "right", "agla slide": "right",
    "agla": "right", "aage": "right", "aage badho": "right",
    "aage karo": "right", "slide badlo": "right", "slide change karo": "right",
    "age badhao": "right", "next karo": "right",

    # --- slides, back ---
    "previous slide": "left", "previous": "left", "back": "left",
    "pichla slide": "left", "pichla": "left", "peeche": "left",
    "peeche jao": "left", "wapas": "left", "piche karo": "left",

    # Natural phrasings LAYA could not rescue. "go back one slide" scored
    # key_action="none" at p=0.35, so it fell through to open_app and tried
    # to find a *file* called "go back one slide" - mid-presentation, the
    # worst possible moment for a file search. Exact entries cost 0 ms and
    # cannot misfire, since the match is on the whole utterance.
    "go back": "left", "go back one slide": "left",
    "back one slide": "left", "one slide back": "left",
    "ek slide peeche": "left", "ek slide wapas": "left",
    "go to the previous slide": "left", "previous one": "left",

    "go forward": "right", "go forward one slide": "right",
    "forward one slide": "right", "one slide forward": "right",
    "next one": "right", "the next one": "right",
    "go to the next slide": "right", "move to the next slide": "right",
    "ek slide aage": "right",

    # --- slides, jump ---
    "first slide": "home", "pehla slide": "home", "shuru se": "home",
    "go to the first slide": "home", "jump to the first slide": "home",
    "go to first slide": "home", "shuru me jao": "home",
    "last slide": "end", "aakhri slide": "end", "aakhiri slide": "end",
    "go to the last slide": "end", "jump to the last slide": "end",
    "go to last slide": "end", "aakhir me jao": "end",

    # --- slideshow on/off ---
    "start presentation": "f5", "presentation chalu karo": "f5",
    "presentation chalu": "f5", "slideshow chalu": "f5",
    "slideshow start": "f5", "present karo": "f5", "presentation start": "f5",
    "present from here": "shift+f5", "yahan se present karo": "shift+f5",
    "end presentation": "esc", "presentation band karo": "esc",
    "band karo": "esc", "stop": "esc", "bas": "esc",

    # --- blank the screen mid-talk ---
    "black screen": "b", "kala karo": "b", "screen kala": "b",
    "white screen": "w", "safed karo": "w",

    # --- scrolling a pdf or doc ---
    "scroll down": "page_down", "neeche": "page_down", "neeche karo": "page_down",
    "scroll up": "page_up", "upar": "page_up", "upar karo": "page_up",

    # --- media, for a video inside the deck ---
    # Hinglish mixes freely, so both halves of each pair get both languages:
    # "volume badhao" was reaching nothing and falling through to a disk
    # search for a file called "volume badhao".
    "volume up": "media_volume_up", "awaaz badhao": "media_volume_up",
    "volume badhao": "media_volume_up", "sound badhao": "media_volume_up",
    "awaaz tez karo": "media_volume_up",
    "volume down": "media_volume_down", "awaaz kam karo": "media_volume_down",
    "volume kam karo": "media_volume_down", "sound kam karo": "media_volume_down",
    "awaaz dheemi karo": "media_volume_down",
    "mute": "media_volume_mute", "mute karo": "media_volume_mute",
    "awaaz band karo": "media_volume_mute",
    "play": "media_play_pause", "pause": "media_play_pause",
    "rok do": "media_play_pause", "chalao": "media_play_pause",

    # --- window ---
    "full screen": "f11", "fullscreen": "f11",
    "switch window": "alt+tab", "next window": "alt+tab",

    # --- window management ---
    # minimize/maximize/restore/close are NOT here on purpose - see
    # WINDOW_COMMANDS below. Snapping stays a keystroke because there is no
    # single win32 call for it, and it only ever acts on the focused window.
    "snap left": "win+left", "left me karo": "win+left",
    "snap right": "win+right", "right me karo": "win+right",
    "desktop dikhao": "win+d", "show desktop": "win+d",
    "sab minimize karo": "win+d",

    # --- tabs ---
    "naya tab": "ctrl+t", "new tab": "ctrl+t", "tab kholo": "ctrl+t",
    "tab band karo": "ctrl+w", "close tab": "ctrl+w",
    "agla tab": "ctrl+tab", "next tab": "ctrl+tab",
    "pichla tab": "ctrl+shift+tab", "previous tab": "ctrl+shift+tab",

    # --- clipboard ---
    "copy karo": "ctrl+c", "copy": "ctrl+c",
    "paste karo": "ctrl+v", "paste": "ctrl+v",
    "cut karo": "ctrl+x", "cut": "ctrl+x",
    "select all": "ctrl+a", "sab select karo": "ctrl+a",
    "undo karo": "ctrl+z", "undo": "ctrl+z",
    "redo karo": "ctrl+y", "redo": "ctrl+y",

    # --- save / print / find ---
    "save karo": "ctrl+s", "save": "ctrl+s",
    "save as": "ctrl+shift+s", "save as karo": "ctrl+shift+s",
    "save kar do": "ctrl+s", "file save karo": "ctrl+s",
    "print karo": "ctrl+p",
    "find karo": "ctrl+f", "search yahan": "ctrl+f",
    "naya window": "ctrl+n", "new window": "ctrl+n",
    "refresh karo": "ctrl+r", "refresh": "ctrl+r",
    "zoom in": "ctrl+=", "zoom badhao": "ctrl+=",
    "zoom out": "ctrl+-", "zoom kam karo": "ctrl+-",

    # --- system ---
    "lock karo": "win+l", "lock": "win+l", "laptop lock karo": "win+l",
    "computer lock karo": "win+l",
    "screenshot lo": "win+shift+s", "screenshot": "win+shift+s",
    "screenshot lelo": "win+shift+s",
}

# Dropped from both ends before matching the table above, so "sparrow next
# slide please" still lands on "next slide". Deliberately tiny: any word
# that appears inside a KEY_COMMANDS phrase must not be in here.
KEY_FILLERS = {"jarvis", "sparrow", "please", "hey", "ok", "okay", "zara", "bhai", "ab", "to", "arre"}

# Words allowed to sit around a key command without changing what it means.
# The table above is matched on a whole phrase, so "minimize kholo" used to
# miss "minimize" entirely and go looking for an app called minimize - and
# "then open new tab" missed "new tab" the same way. The matcher now tries
# shorter runs of the sentence, and this set is what makes that safe.
#
# The rule for adding a word: it must say how to act, never what to act on.
# "desktop" is a target, so it stays out, and that single omission is what
# stops "desktop pe wapas jao" from matching the "wapas" entry and firing a
# Left arrow instead of opening the folder. Verbs and sequencing words are
# fine - they carry no target of their own. Unlike KEY_FILLERS, words here
# MAY appear inside a KEY_COMMANDS phrase: the longest run always wins, so
# "tab kholo" still matches itself before "kholo" is ever shaved off.
KEY_WRAPPERS = KEY_FILLERS | {
    # sequencing - "one command at a time" means these only ever mean "next"
    "abhi", "now", "then", "phir", "fir", "uske", "baad", "aur", "and",
    # bare verbs of doing. They say "perform it", never what to perform.
    "karo", "kar", "kardo", "karna", "karde", "kardein",
    "do", "dedo", "dena", "de", "lo", "lelo", "liya",
    "kholo", "khol", "kholna", "open", "press", "dabao", "dabado",
    # pronouns and articles, which point at the current window, not a name
    "ye", "yeh", "isko", "iss", "is", "it", "this", "the", "a", "an",
    "ek", "baar", "mujhe", "jara",
    # "window" belongs with them, for the same reason they are here: in a
    # command it points at the one in front rather than naming a target.
    # Without it, "minimize the window" left "window" outside the match and
    # was refused, because the table happens to carry "window minimize karo"
    # and not "minimize window" - a word-order accident deciding whether a
    # command works. "close window" and "new window" are whole phrases in
    # the tables and still match as themselves.
    "window", "windows",
}

# Phrase -> what to do to a window, handled by state.py rather than by a
# keystroke. These four used to be Win+Down / Win+Up / Alt+F4, and all three
# of those are the wrong tool:
#
#   restore  - a minimised window has no focus, so there is nothing for a
#              keystroke to land on. It can only be done by naming the
#              window, which means remembering which one you minimised.
#   close    - Alt+F4 with no window focused is the Windows shutdown dialog.
#              A WM_CLOSE to one window handle cannot do that.
#   minimise - worked, but asking the window directly is verifiable: the
#              minimised flag can be read back, where a sent key cannot.
#
# Matched by the same longest-run matcher as KEY_COMMANDS, so the KEY_WRAPPERS
# words are tolerated around these too.
WINDOW_COMMANDS = {
    "minimize": "minimize", "minimize karo": "minimize",
    "window minimize karo": "minimize", "ise minimize karo": "minimize",
    "chhota karo": "minimize",

    "maximize": "maximize", "maximize karo": "maximize",
    "window maximize karo": "maximize", "bada karo": "maximize",
    "full karo": "maximize",

    "restore": "restore", "restore window": "restore",
    "restore karo": "restore", "window restore karo": "restore",
    "wapas lao": "restore", "window wapas lao": "restore",
    "wapas dikhao": "restore", "window wapas dikhao": "restore",
    "undo minimize": "restore",

    "close window": "close", "window band karo": "close",
    "ye band karo": "close", "ise band karo": "close",
    "window close karo": "close", "close this window": "close",
    "is window ko band karo": "close",
}

# ------------------------------------------------------------------- typing
# Where dictated text is allowed to land.
#
# type_text used to decide this by looking for the app's own name, "python" or
# "powershell" in the window TITLE, which is wrong in both directions. A
# Notepad file called "python notes.txt" was refused, and a console with a
# renamed title was not. Both of those are the same mistake: a title is
# something the user types, not something the system knows.
#
# So this is keyed on the owning process, and the console is identified by
# asking Windows for it outright - see state.console_handle().

# Processes a dictated line may be sent to when one of them is ALREADY the
# window in front. Editors first, browsers after, because the order here is
# also the preference order used when type_text has to go and find a target.
TYPE_SAFE_APPS = (
    "notepad", "notepad++", "wordpad", "write", "code", "sublime_text",
    "winword", "excel", "onenote", "devenv", "idle", "gvim", "atom",
    "chrome", "msedge", "firefox", "brave", "opera",
)

# Processes that may be chosen AUTOMATICALLY when the window in front cannot
# be typed into. A deliberately shorter list than TYPE_SAFE_APPS: a browser
# is a fine target when you are already looking at it and chose it yourself,
# but going off and picking one unprompted can put a dictated sentence into a
# page, an address bar, or a keyboard shortcut. A text editor has one place
# for text to go, which is what makes it safe to pick without asking.
TYPE_AUTO_TARGETS = (
    "notepad", "notepad++", "wordpad", "write", "code", "sublime_text",
    "winword", "onenote",
)

# Never, under any circumstances. The console is where LAYA itself is
# running: text sent there is typed at a shell prompt, and a dictated
# sentence at a shell prompt is a command nobody asked to run. This list is
# the backstop for terminals that host the process without owning the
# console window (Windows Terminal tabs, an IDE's integrated shell);
# state.console_handle() is the exact answer and is checked first.
TYPE_BLOCKED_APPS = (
    "python", "pythonw", "py", "cmd", "powershell", "pwsh",
    "windowsterminal", "openconsole", "conhost", "wt",
)

# Spoken ways of naming the window to type into: "type on notepad hello",
# "notepad mein likho hello". Mapped through APP_ALIASES afterwards, so
# anything already nameable as an app is nameable as a typing target.
TYPE_TARGET_PREPOSITIONS = ("on", "in", "into", "to", "me", "mein", "pe", "par")

# A slide change every few seconds plus a spoken confirmation each time gets
# old fast in a live talk. The console still logs it. Flip to True if you
# want the earbuds to confirm.
SPEAK_KEY_ACTIONS = False

# ---------------------------------------------------------------- search
# Everything's CLI. Found automatically if it sits next to this file or on PATH.
ES_EXE_CANDIDATES = [
    PROJECT / "es.exe",
    Path(r"C:\Program Files\Everything\es.exe"),
    Path(r"C:\Program Files (x86)\Everything\es.exe"),
]
# Used only when es.exe is missing, so the bot still works on a fresh machine.
SEARCH_ROOTS = [
    HOME / "Desktop",
    HOME / "Documents",
    HOME / "Downloads",
    HOME / "OneDrive",
    HOME / "Pictures",
]
FALLBACK_INDEX_MAX_DEPTH = 6
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", "venv", ".venv", "AppData",
    "site-packages", ".next", "dist", "build", ".cache",
}

# Everything indexes the whole drive, which means AppData lock files and
# "Recent" shortcuts flood the results. Drop anything living in here.
EXCLUDE_PATH_PARTS = (
    "\\appdata\\", "\\windows\\", "\\program files", "\\programdata\\",
    "\\$recycle.bin\\", "\\node_modules\\", "\\__pycache__\\", "\\.git\\",
    "\\.claude\\", "\\site-packages\\", "\\temp\\", "\\tmp\\",
)
# Office lock/autosave droppings and dotfiles.
EXCLUDE_NAME_PREFIXES = (".~lock", "~$", ".")

# "the doc of that" -> which extensions count as a doc
FILE_TYPES = {
    "doc": [".docx", ".doc", ".odt", ".rtf", ".md", ".txt", ".pages"],
    "pdf": [".pdf"],
    "sheet": [".xlsx", ".xls", ".csv", ".ods"],
    "slides": [".pptx", ".ppt", ".odp"],
    "image": [".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"],
    "video": [".mp4", ".mkv", ".mov", ".avi", ".webm"],
    "code": [".py", ".js", ".ts", ".tsx", ".java", ".cpp", ".json", ".html", ".css"],
    "any": [],
}

# ---------------------------------------------------------------- apps
# Left side: what you might actually say. Right side: what Windows launches.
APP_ALIASES = {
    "chrome": "chrome", "browser": "chrome", "google chrome": "chrome",
    "edge": "msedge", "firefox": "firefox", "brave": "brave",
    "vs code": "code", "vscode": "code", "code": "code", "editor": "code",
    "notepad": "notepad", "note pad": "notepad",
    "calculator": "calc", "calc": "calc",
    "paint": "mspaint", "ms paint": "mspaint",
    "terminal": "wt", "cmd": "cmd", "command prompt": "cmd",
    "powershell": "powershell", "explorer": "explorer",
    "file manager": "explorer", "files": "explorer",
    "spotify": "spotify", "discord": "discord", "slack": "slack",
    "whatsapp": "whatsapp", "telegram": "telegram",
    "task manager": "taskmgr", "settings": "ms-settings:",
    "control panel": "control", "snipping tool": "snippingtool",
    "word": "winword", "excel": "excel", "powerpoint": "powerpnt",
    "outlook": "outlook", "steam": "steam", "zoom": "zoom",
    "obs": "obs64", "postman": "postman", "figma": "figma",
}

# Which of the aliases above are browsers. Naming one plus anything else
# ("open chrome search harsh tiwari portfolio") means a web search in that
# browser - launching Chrome and silently dropping the rest of the sentence
# is the most annoying possible way to get this wrong.
BROWSERS = {"chrome", "google chrome", "browser", "edge", "firefox", "brave"}

# Spoken name -> folder. Checked before any file search.
FOLDER_ALIASES = {
    "downloads": HOME / "Downloads", "download": HOME / "Downloads",
    "desktop": HOME / "Desktop",
    "documents": HOME / "Documents", "docs": HOME / "Documents",
    "pictures": HOME / "Pictures", "photos": HOME / "Pictures",
    "videos": HOME / "Videos", "music": HOME / "Music",
    "onedrive": HOME / "OneDrive", "home": HOME,
    "recycle bin": Path("shell:RecycleBinFolder"),
}

# Words to strip when pulling the search term out of a sentence.
# Hinglish included: "food4all ka doc kholo" -> "food4all"
STOPWORDS = {
    "open", "launch", "start", "run", "show", "find", "search", "for",
    "the", "a", "an", "my", "me", "of", "that", "this", "it", "please",
    "in", "on", "windows", "window", "computer", "laptop", "pc", "system",
    "file", "folder", "doc", "docs", "document", "pdf", "sheet", "excel",
    "slides", "presentation", "image", "photo", "picture", "video",
    "and", "then", "also", "jarvis", "sparrow", "hey", "ok", "okay", "up",
    "kholo", "khol", "kholna", "dikhao", "dikha", "chalu", "karo", "kar",
    "ka", "ki", "ke", "ko", "wala", "wali", "mera", "meri", "mujhe",
    "dhundo", "dhoondo", "nikalo", "laao", "la", "de", "do", "bhai",
    "jo", "hai", "wo", "woh", "usko", "uska", "zara", "please",
    "pe", "par", "jao", "jaa", "ja", "chalo", "chal", "wapas",
    # Sequencing filler. One trigger = one command, so these only ever
    # mean "here comes the next one" - never part of the target name.
    "now", "ab", "abhi", "phir", "fir", "uske", "baad",
}


# --------------------------------------------------------- whisper vocabulary
# Derived from the command tables rather than written out, because the
# hand-written version drifted and that drift was a real bug.
#
# WHISPER_PROMPT listed only launch phrases - "chrome kholo", "doc kholo",
# "notepad kholo". Not one action word: no save, no close, no minimize, no
# copy, no click. Every one of those commands was added to the tables after
# the prompt was written, and nobody went back. So the decoder was being
# primed toward a vocabulary that did not contain the word being spoken, and
# on a one-second clip the prompt is most of the context it has. That is how
# "save" came out as "see you" and "Harsh Tiwari portfolio" as "herstiva
# report for you".
#
# Deriving it means the next command added to a table is in the vocabulary
# the same day, with nobody having to remember.
def _command_words() -> list[str]:
    """Every distinct word LAYA can be commanded with, action words first.

    Order matters: faster-whisper truncates hotwords to half the context
    window, and truncation keeps the head. Action verbs are what Whisper
    actually loses, so they go first and the long tail of app names last.
    """
    seen: list[str] = []

    def add(words) -> None:
        for word in words:
            word = word.strip().lower()
            if word and word not in seen:
                seen.append(word)

    for table in (WINDOW_COMMANDS, KEY_COMMANDS):
        for phrase in table:
            add(phrase.split())
    add(_TYPE_TRIGGER_WORDS)
    for alias in APP_ALIASES:
        add(alias.split())
    add(NAME_ALIASES.values())
    return seen


# Kept here rather than imported from router, which imports this module.
_TYPE_TRIGGER_WORDS = ("likho", "likh", "type", "write", "save", "close",
                       "open", "search", "click", "scroll", "select")

WHISPER_HOTWORDS = " ".join(_command_words())

# A fluent sentence, not a word list: Whisper was trained to continue a
# transcript, so natural phrasing primes it better than a glossary. The
# glossary goes in WHISPER_HOTWORDS above, which has its own slot in the
# decoder prompt - both are used, they do not compete.
WHISPER_PROMPT = (
    "Hinglish laptop commands: chrome kholo, notepad kholo, food4all kholo. "
    "Save karo, close the window, minimize karo, maximize karo. "
    "Copy karo, paste karo, select all, new tab, close tab. "
    "Search for Harsh Tiwari portfolio. Click the second link. Go back."
)
