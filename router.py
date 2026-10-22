"""Turn a spoken sentence into (intent, file_type, search_term).

LAYA classifies. It does not extract - it has no text generation, so the
search term is pulled out with plain string work.

Order matters, and it is keywords first. Measured on this laptop (CPU,
dGPU off): keyword matching 3ms, LAYA 1100-1800ms. LAYA also loses on
file_type - for "food4all ka doc kholo" it returned `any` at p=0.91 while
`doc` sat at p=0.08, even though "doc" is right there in the sentence.

So:

    keywords  -> intent + file_type        (3ms, handles the phrasings you listed)
    LAYA      -> only when keywords shrug  (~1.2s, handles the ones you didn't)
    Python    -> which words are the target

That keeps the 20 commands you say daily instant, and still understands a
sentence nobody put in the table. Set SPARROW_LAYA_FIRST=1 to flip the order
and feel the difference.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import click_target
import config
import spoken_keys
from action_schema import Action

INTENTS = ("open_app", "open_file", "open_folder", "web_search", "youtube_channel", "press_keys", "type_text", "close_app", "unknown")

# What LAYA's key_action labels actually press. Only used on the fallback
# path - an exact phrase from config.KEY_COMMANDS brings its own key.
KEY_ACTION_SPECS = {
    "next": "right",
    "previous": "left",
    "first": "home",
    "last": "end",
    "start_show": "f5",
    "end_show": "esc",
    "blank": "b",
    "scroll_down": "page_down",
    "scroll_up": "page_up",
}

QUESTIONS = {
    "intent": {
        "type": "choice",
        "instructions": "What does the user want the computer to do?",
        "criteria": {
            "open_app": "launch a program or application, e.g. chrome, vs code, spotify, calculator",
            "open_file": "find and open a specific document, pdf, sheet, image or file by its name",
            "open_folder": "open a folder or directory in the file explorer",
            "web_search": "look something up on the internet or on google",
            "youtube_channel": "open YouTube, search for a channel, and play the first video from that channel",
            "press_keys": "control what is already on screen - change slides, go forward or back in a presentation, scroll, blank the screen, start or stop a slideshow, adjust volume",
            "type_text": "type, write, or enter specific text into the current window or application",
            "unknown": "anything else, or not a request to open something",
        },
    },
    "key_action": {
        "type": "choice",
        "instructions": "If the user is controlling something already on screen, which control is it?",
        "criteria": {
            "next": "move forward one slide or page",
            "previous": "move back one slide or page",
            "first": "jump to the very first slide",
            "last": "jump to the very last slide",
            "start_show": "begin the slideshow or presentation mode",
            "end_show": "exit the slideshow or stop presenting",
            "blank": "blank or black out the screen",
            "scroll_down": "scroll down the page",
            "scroll_up": "scroll up the page",
            "none": "the user is not controlling the screen",
        },
    },
    "file_type": {
        "type": "choice",
        "instructions": "Which kind of file is the user asking for?",
        "criteria": {
            "doc": "a document, word file, notes, readme or writeup",
            "pdf": "a pdf specifically",
            "sheet": "a spreadsheet, excel file or csv",
            "slides": "a presentation or slide deck",
            "image": "a picture, photo, screenshot or image",
            "video": "a video or movie file",
            "code": "a source code file or script",
            "any": "no particular file type mentioned",
        },
    },
}


@dataclass
class Intent:
    name: str
    file_type: str
    term: str
    source: str  # "laya" or "keywords" - handy when debugging a misfire
    confidence: float = 0.0
    # Which window the action is aimed at, when the sentence named one.
    # Only type_text fills this in today: "type on notepad hello world" has
    # to focus Notepad and type "hello world", so the app name and the
    # payload have to travel separately or the app name gets typed too.
    target: str = ""

    def to_action(self) -> Action:
        """Convert the compatibility intent into the execution contract."""
        if self.name == "press_keys":
            parameters = {"spec": self.term}
        elif self.name == "type_text":
            parameters = {"text": self.term, "target": self.target}
        else:
            parameters = {"term": self.term}
            if self.name == "open_app":
                parameters = {"app": self.term, "term": self.term}
        if self.file_type != "any":
            parameters["file_type"] = self.file_type
        return Action(self.name, parameters, self.source, self.confidence)


# ------------------------------------------------------------------ laya
_router = None
_laya_failed = False


def _get_laya():
    """Load LAYA once. First call downloads a checkpoint (~650 MB)."""
    global _router, _laya_failed
    if os.environ.get("SPARROW_NO_LAYA"):
        return None  # force the keyword path, handy for A/B testing
    if _router is not None or _laya_failed:
        return _router
    try:
        from laya import Router

        _router = Router()
    except Exception as exc:  # not installed, no model, offline - all fine
        print(f"[laya] unavailable, using keywords ({type(exc).__name__}: {exc})")
        _laya_failed = True
    return _router


def _answer(answers: dict, key: str) -> tuple[str | None, float]:
    """LAYA's result shape varies by version, so read it defensively."""
    slot = answers.get(key)
    if not isinstance(slot, dict):
        return (slot if isinstance(slot, str) else None), 0.0

    label = slot.get("choice") or slot.get("label") or slot.get("answer")
    conf = 0.0
    # answer_confidence is the probability of the chosen label - the number
    # we actually want. "confidence" is a separate margin-style score that
    # runs much lower (0.21 for a correct, obvious answer), and this
    # checkpoint warns that it ships uncalibrated anyway.
    for name in ("answer_confidence", "confidence", "probability", "p"):
        value = slot.get(name)
        if isinstance(value, (int, float)):
            conf = float(value)
            break
    return label, conf


def _classify_with_laya(text: str) -> tuple[str, str, float, str] | None:
    """(intent, file_type, confidence, key_action) or None if it can't say."""
    router = _get_laya()
    if router is None:
        return None
    try:
        result = router.predict(text, QUESTIONS)
    except Exception as exc:
        print(f"[laya] predict failed: {exc}")
        return None

    answers = result.get("answers", {}) if isinstance(result, dict) else {}
    intent, conf = _answer(answers, "intent")
    file_type, _ = _answer(answers, "file_type")
    key_action, key_conf = _answer(answers, "key_action")

    # key_action outranks intent, and that is backed by measurement rather
    # than taste. Probed on this laptop over 16 sentences:
    #
    #   not screen control (10)  -> said "none" every time. Zero misfires.
    #   screen control      (6)  -> named the right key on 5
    #                               (first 0.99, last 0.98, scroll 0.89,
    #                                next 0.84, blank 0.45)
    #
    # The intent slot meanwhile answered "open_app" for 14 of those 16,
    # including "scroll down a bit" at p=0.87. It degraded when
    # youtube_channel and type_text joined the choice list - LAYA is
    # non-autoregressive, so every label competes with every other one and
    # adding two shifts all the rest.
    #
    # Checked before the confidence gate below on purpose: key_action
    # carries its own high confidence, and throwing it away because the
    # intent slot is muddy is how "jump to the first slide" (key p=0.99)
    # ended up launching an app.
    if key_action in KEY_ACTION_SPECS:
        return ("press_keys", "any", key_conf, key_action)

    if intent not in INTENTS:
        return None
    if conf and conf < config.MIN_CONFIDENCE:
        return None  # not sure enough - let keywords decide instead
    return (
        intent,
        (file_type if file_type in config.FILE_TYPES else "any"),
        conf,
        "",
    )


# ------------------------------------------------------------------ keywords
_APP_WORDS = ("open", "launch", "start", "run", "chalu", "kholo", "khol")
_SEARCH_WORDS = ("google", "search the web", "internet", "web search", "youtube")
# A bare search verb. Only means the web when nothing points at this laptop.
_SEARCH_VERBS = ("search", "dhundo", "dhoondo", "khojo", "lookup")
# These say "the thing I want is on this machine", so keep it a file search.
_LOCAL_HINTS = ("my", "mera", "meri", "mere", "apna", "apni", "wala", "wali")
_FOLDER_WORDS = ("folder", "directory", "explorer")
_FILE_WORDS = ("file", "doc", "document", "pdf", "sheet", "excel", "report",
               "notes", "presentation", "image", "photo", "screenshot", "video")
# Directions. A slide word sitting next to one of these is screen control,
# not a file request - "go back one slide" is not asking for a .pptx.
_DIRECTION_WORDS = ("next", "back", "forward", "previous", "last", "first",
                    "aage", "peeche", "pichla", "agla", "wapas")
# YouTube intent triggers
_YOUTUBE_WORDS = ("youtube", "yt", "tuub", "tuube")
_YOUTUBE_VERBS = ("search", "dhundo", "dhoondo", "khojo", "play", "chalao", "kholo",
                  "karo", "kijiyega", "dikhao", "dikhayo", "daalo", "chala")

# Text-typing triggers: "likh hello world", "type my name is harsh"
_TYPE_WORDS = ("likh", "likho", "likhna", "likho", "write", "type", "enter")

# Words that mean "shut this app down". They exist because of one line in a
# live session: "close chrome" came back open_app with the term "close
# chrome", and LAYA answered "chrome saamne le aaya" - it brought to the
# front the window you had just asked it to close.
#
# The cause was that naming an app was treated as evidence of wanting to
# launch it, with no attention paid to the verb in front of the name. So
# close and open were the same command with different noise around them.
# A verb from this list says the opposite, and it is checked before those
# launch rules rather than after.
_CLOSE_VERBS = ("close", "closed", "quit", "exit", "kill", "terminate",
                "band", "bandh", "bando", "khatam")


def _has_close_verb(text: str) -> bool:
    """Does this sentence ask for something to be shut down?"""
    return any(re.search(rf"\b{w}\b", text) for w in _CLOSE_VERBS)

# The bare nouns allowed to mean "a thing on this laptop" with no verb at
# all. Only what you have taught it, because everything else unrecognised
# now asks instead of searching the disk - see the end of route().
_KNOWN_NAMES = {value.lower() for value in config.NAME_ALIASES.values()}


def _looks_like_youtube_request(text: str) -> bool:
    """Heuristic: user wants YouTube, not a website search.

    Whole words only, and that is not a detail: "yt" is a substring of
    "python", so "search the web for python tutorials" used to be read as a
    YouTube request - and then the channel name had the "yt" cut out of it
    and went looking for a channel called "phon tutorials".
    """
    has_yt = any(re.search(rf"\b{w}\b", text) for w in _YOUTUBE_WORDS)
    has_verb = any(re.search(rf"\b{w}\b", text) for w in _YOUTUBE_VERBS)
    has_search = any(re.search(rf"\b{w}\b", text) for w in _SEARCH_VERBS)
    return has_yt and (has_verb or has_search)


def _guess_file_type(text: str) -> str:
    table = {
        "doc": ("doc", "document", "word", "notes", "writeup", "readme", "md"),
        "pdf": ("pdf",),
        "sheet": ("sheet", "excel", "spreadsheet", "csv"),
        "slides": ("slide", "ppt", "presentation", "deck"),
        "image": ("image", "photo", "picture", "screenshot", "png", "jpg"),
        "video": ("video", "movie", "clip", "mp4"),
        "code": ("code", "script", "py", "python", "source"),
    }
    for file_type, words in table.items():
        if any(re.search(rf"\b{w}\b", text) for w in words):
            return file_type
    return "any"


_QUESTION_STARTERS = (
    "what", "who", "when", "where", "why", "how", "which", "whose",
    "is", "are", "does", "do", "can", "should",
    "kya", "kaun", "kab", "kahan", "kaise", "kitna", "kitne", "kyun",
)


def _looks_like_question(text: str) -> bool:
    """A question is something to look up, not a file to open."""
    if "?" in text:
        return True
    first = text.split()[0] if text.split() else ""
    return first in _QUESTION_STARTERS


def _has_search_verb(text: str) -> bool:
    return (
        any(w in text for w in _SEARCH_WORDS)
        or any(re.search(rf"\b{w}\b", text) for w in _SEARCH_VERBS)
    )


# Words that actually say "there is something to open here". The gate below
# needs at least one of them before it will believe a launch.
_LAUNCH_SIGNAL_WORDS = (
    _APP_WORDS + _FILE_WORDS + _FOLDER_WORDS + _SEARCH_VERBS
    + ("dikhao", "dikha", "dikhayo", "jao", "chalao", "chala", "play")
)


def _has_launch_signal(text: str, term: str) -> bool:
    """Is there anything in this sentence that really points at something?

    LAYA will label almost any leftover utterance open_app, and open_app
    falls through to a disk search, so "uh um" opened a file out of a cache
    directory and "harsh tiwari portfolio" opened one out of .gemini. Both
    came back above the confidence floor; confidence alone is not evidence.

    What counts as evidence: a verb that means open or find, a noun that
    means file or folder, an app you have an alias for, or a name you taught
    it in NAME_ALIASES. With none of those, a launch verdict is a guess, and
    guessing here means running something you never asked for.
    """
    if _match_alias_name(text) or _match_browser_name(text):
        return True

    name = (term or "").strip().lower()
    if name and (name in _KNOWN_NAMES or name in config.FOLDER_ALIASES):
        return True

    return any(re.search(rf"\b{w}\b", text) for w in _LAUNCH_SIGNAL_WORDS)


def _classify_with_keywords(text: str, term: str) -> tuple[str, str]:
    file_type = _guess_file_type(text)
    alias = _match_alias_name(text)

    # Typing text into the active window
    if any(re.search(rf'\b{w}\b', text) for w in _TYPE_WORDS):
        return "type_text", "any"

    # YouTube FIRST — before the browser+search check, otherwise
    # "open chrome and search for yt ..." becomes web_search and never
    # reaches YouTube. yt/youtube is the stronger signal here.
    if _looks_like_youtube_request(text):
        return "youtube_channel", "any"

    # Naming a browser *and* a search verb means the web, full stop - it
    # beats every local-file hint below. "open chrome search harsh tiwari
    # portfolio" must not launch Chrome and drop the rest of the sentence.
    browser = _match_browser_name(text)
    if browser and _has_search_verb(text) and extract_query(text, drop=browser):
        return "web_search", file_type

    if any(w in text for w in _SEARCH_WORDS):
        return "web_search", file_type

    if _looks_like_youtube_request(text):
        return "youtube_channel", "any"

    # "search harsh tiwari portfolio" with nothing file-ish about it is a
    # web search. "search for my resume" is not - a possessive or a file
    # word means you mean something on this laptop.
    #
    # This deliberately does NOT care what file_type was inferred, and that
    # is the whole point. file_type is guessed from the words in the
    # sentence, so "search for movie" came back as video and "search for
    # python tutorial" as code - and requiring file_type == "any" here then
    # dropped both of them through to open_file. "search for movie" would
    # have opened C:\Acer Diagnostic Suite Toolkit\...\VGADevice\..., a
    # random vendor driver file, and called it a success.
    #
    # The word was part of the QUERY, not a statement about where to look.
    # What actually says "look on this laptop" is a file word or a
    # possessive, and both are already checked below; the inferred type adds
    # nothing except a way for an innocent noun to turn a search into a
    # launch. That is exactly the "never guess your way into opening a
    # random file" rule, so the guess does not get a vote.
    #
    # The youtube check above has to stay above: "yt pe movie search karo"
    # is a search verb with no file word, and it belongs to youtube_channel
    # rather than to Google.
    if (
        any(re.search(rf"\b{w}\b", text) for w in _SEARCH_VERBS)
        and not any(w in text for w in _FILE_WORDS)
        and not any(re.search(rf"\b{w}\b", text) for w in _LOCAL_HINTS)
        and extract_query(text, drop=alias)
    ):
        return "web_search", "any"

    # A bare folder name is a folder: "downloads kholo" -> term is "downloads".
    if term in config.FOLDER_ALIASES:
        return "open_folder", file_type

    # A close verb aimed at an app, and the position in this function is the
    # whole point. It sits AFTER every web-search test, because "search for
    # close combat games" is a search and the word "close" in it is part of
    # the query - and BEFORE the two alias rules below, because those fire on
    # an app name with no attention paid to the verb in front of it. That is
    # what made close and open the same command: "close chrome" came back
    # open_app with the term "close chrome", and LAYA said "chrome saamne le
    # aaya" - it fronted the window you asked it to close.
    #
    # "close window", "close tab" and "ye band karo" never arrive here. They
    # are whole phrases in WINDOW_COMMANDS and KEY_COMMANDS, which route()
    # consults before this function runs.
    #
    # `term` with no alias is allowed on purpose, so "close spotify" works
    # for an app that was never aliased. It is safe because close_app only
    # ever closes a window that is ALREADY open and launches nothing: an
    # unrecognised name finds no window and says so.
    if _has_close_verb(text) and (alias or term):
        return "close_app", "any"

    # If an app name is all the command contains, launch it - even when the
    # name collides with a file word. "excel kholo" is the program;
    # "food4all ka excel kholo" leaves "food4all" behind, so it's a file.
    if alias and not extract_term(re.sub(rf"\b{re.escape(alias)}\b", " ", text)):
        return "open_app", file_type

    if any(w in text for w in _FOLDER_WORDS):
        return "open_folder", file_type

    # A search verb aimed at this laptop: "search for my resume", "mera
    # resume dhundo". This has to answer outright, not just decline to say
    # web_search - leaving it unknown hands the sentence to LAYA, which
    # reads "search" and sends you to Google.
    if _has_search_verb(text) and any(
        re.search(rf"\b{w}\b", text) for w in _LOCAL_HINTS
    ):
        return "open_file", file_type

    # A known app name plus an open-ish verb is almost always a launch.
    if alias and not any(w in text for w in _FILE_WORDS):
        return "open_app", file_type

    # "go back one slide" names a slide but wants an arrow key. Abstain and
    # let LAYA pick the direction - that is exactly what it is here for.
    # Opening a .pptx mid-talk because the sentence said "slide" is the bad
    # outcome, and file_type="slides" alone would cause it.
    if file_type == "slides" and any(
        re.search(rf"\b{w}\b", text) for w in _DIRECTION_WORDS
    ):
        return "unknown", "any"

    if any(w in text for w in _FILE_WORDS) or file_type != "any":
        return "open_file", file_type
    if any(w in text for w in _APP_WORDS):
        return "open_app", file_type
    return "unknown", file_type


# ------------------------------------------------------------------ term
# Only the words that trigger a web search. Everything else is the query,
# because "best laptop under 50000" must keep "laptop".
_QUERY_TRIGGERS = {
    "google", "search", "karo", "kar", "web", "internet", "lookup", "look",
    "up", "for", "online", "please", "jarvis", "sparrow", "hey", "ok", "okay", "the",
    "dhundo", "dhoondo", "pe", "par", "me", "youtube", "open", "kholo",
    # Possessives are noise in a web query - Google has no idea whose.
    # Still a local-file hint in _LOCAL_HINTS, which runs before this.
    "my", "mera", "meri", "mere", "apna", "apni",
    # Sequencing words. Every trigger press is its own command, so "now
    # search for harsh tiwari portfolio" is just a search - the "now" is
    # you talking to me, not part of what you want looked up. Without
    # this the query went out as "now harsh tiwari portfolio".
    "now", "ab", "abhi", "then", "phir", "fir", "and", "aur",
}


def extract_term(text: str) -> str:
    """Strip the filler and keep the target. 'food4all ka doc kholo' -> 'food4all'."""
    cleaned = re.sub(r"[^\w\s.&+-]", " ", text.lower())
    kept = [w for w in cleaned.split() if w and w not in config.STOPWORDS]

    # Drop a trailing bare extension: "food4all docx" -> "food4all"
    if len(kept) > 1 and kept[-1] in {"docx", "pdf", "xlsx", "pptx", "png", "txt", "md"}:
        kept.pop()
    return " ".join(kept).strip()


def extract_query(text: str, drop: str | None = None) -> str:
    """Web-search variant: keep the content words, drop only the triggers.

    `drop` removes a named app, so "open chrome search harsh tiwari
    portfolio" looks up the person and not the browser.
    """
    text = (text or "").lower()
    if drop:
        text = re.sub(rf"\b{re.escape(drop)}\b", " ", text)
    cleaned = re.sub(r"[^\w\s.&+#-]", " ", text)
    kept = [w for w in cleaned.split() if w and w not in _QUERY_TRIGGERS]
    return " ".join(kept).strip()


def extract_text_payload(text: str) -> str:
    """Pull the text-to-type from a command like 'likh hello world'.

    Everything after the trigger word (likh/write/type/etc.) is the payload.
    We keep ALL words - no stopword stripping, because the user dictated
    exactly what they want typed.
    """
    text = (text or "").strip()
    # Find the trigger word and take everything after it
    for trigger in _TYPE_WORDS:
        match = re.search(rf'\b{trigger}\b\s*', text, re.IGNORECASE)
        if match:
            payload = text[match.end():].strip()
            # Remove trailing punctuation Whisper sometimes adds
            payload = payload.rstrip('.!?')
            if payload:
                return payload
    return text  # fallback: the whole thing


def extract_type_target(text: str) -> tuple[str, str]:
    """Which window to type into and what to type: ("notepad", "hello world").

    "type on notepad hello world" has to focus Notepad and type "hello
    world" - not "on notepad hello world", which is what happens when the
    payload is taken as everything after the trigger.

    A preposition between the verb and the app name is REQUIRED, and that is
    the safety rule rather than a grammar preference. Without it, "type
    notepad is slow" would name Notepad as a target and type " is slow", and
    "write notepad in the list" would lose the word it was told to write.
    The preposition is the only thing distinguishing "the window I mean" from
    "a word I am dictating", so when it is absent there is no target and the
    whole payload is text - which is the existing behaviour, unchanged.

    Two word orders, because you speak both:

        type on notepad hello world     verb, preposition, app, text
        notepad mein type karo hello    app, preposition, verb, text

    Returns ("", payload) when no window was named.
    """
    text = (text or "").strip()
    payload = extract_text_payload(text)
    if not text:
        return "", payload

    verbs = "|".join(_TYPE_WORDS)
    preps = "|".join(config.TYPE_TARGET_PREPOSITIONS)
    # Longest alias first so "google chrome" wins over "chrome", and so
    # "note pad" is not read as the single word "pad".
    aliases = "|".join(re.escape(a) for a in
                       sorted(config.APP_ALIASES, key=len, reverse=True))

    for pattern, trailing_verb in (
        # "type on notepad hello world" - the verb already came first, so
        # every word after the app name is dictated text. Nothing is
        # stripped here: "type on notepad the quick brown fox" has to keep
        # its "the", and "the" is in KEY_WRAPPERS, so reusing that list
        # would have eaten it.
        (rf"\b(?:{verbs})\b\s+(?:{preps})\s+\b({aliases})\b\s*(.*)$", False),
        # "notepad mein type karo hello world" - Hinglish puts the verb's
        # other half after it, and that half is not part of the line.
        (rf"\b({aliases})\b\s+(?:{preps})\s+\b(?:{verbs})\b\s*(.*)$", True),
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        spoken, rest = match.group(1).lower(), match.group(2).strip()
        if trailing_verb:
            # Only the words that finish the verb - "karo", "do", "dena".
            # Deliberately not KEY_WRAPPERS: that list exists to find a
            # target and contains articles, which a dictated sentence is
            # allowed to start with.
            rest = re.sub(r"^(?:kar|karo|kardo|karna|karde|do|dedo|dena|de)\b\s*",
                          "", rest, flags=re.IGNORECASE).strip()
        return config.APP_ALIASES.get(spoken, spoken), rest.rstrip(".!?")

    return "", payload


def _match_alias_name(text: str) -> str | None:
    """The spoken alias itself. Longest first, so 'vs code' beats 'code'."""
    for alias in sorted(config.APP_ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", text):
            return alias
    return None


def _match_browser_name(text: str) -> str | None:
    """The browser named in this sentence, scanned on its own.

    Not derived from _match_alias_name: that returns one alias for the
    whole sentence, longest wins, so "open chrome search spotify playlists"
    hands back "spotify" and the browser disappears.
    """
    for alias in sorted(config.BROWSERS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", text):
            return alias
    return None


def match_app(text: str) -> str | None:
    """What Windows should launch for the app name in this sentence."""
    alias = _match_alias_name(text)
    return config.APP_ALIASES[alias] if alias else None


def match_browser(text: str) -> str | None:
    """What Windows should launch for the browser named here, or None."""
    alias = _match_browser_name(normalize(text))
    return config.APP_ALIASES.get(alias) if alias else None


# ------------------------------------------------------------------ cleanup
# Whisper punctuates like prose, because it was trained on prose. Every
# utterance comes back as a sentence: "Open food4all." Those marks then
# ride along into the search term and quietly break it.
_EDGE_PUNCT = ".,!?;:'\"()[]{}…"


def normalize(text: str) -> str:
    """Repair what speech-to-text got wrong, before anything reads it.

    Three separate problems. Whisper punctuates every sentence, so "open
    food4all." searches for a file literally named `food4all.` - and an
    extension word like "document." stops matching the stopword list that
    was supposed to strip it. It writes "colo" for "kholo" because it is
    pinned to English. And it writes "food for all" for "food4all" because
    that is honestly what the words sound like.

    Punctuation goes first on purpose: it turns "colo." into "colo", which
    SPOKEN_FIXES then knows how to repair. The other way round, every fix
    would need a second entry with a full stop glued to it.
    """
    text = (text or "").strip().lower()
    if not text:
        return ""

    # Phrases first, longest first, so "food four all" is not half-eaten by
    # a shorter alias that also matches.
    for spoken in sorted(config.NAME_ALIASES, key=len, reverse=True):
        if spoken in text:
            text = text.replace(spoken, config.NAME_ALIASES[spoken])

    words = []
    for word in text.split():
        # Only the edges. An inner dot is part of the name - "document.js"
        # and "v2.0" have to survive this.
        word = word.strip(_EDGE_PUNCT)
        if word:
            words.append(config.SPOKEN_FIXES.get(word, word))
    return " ".join(words).strip()


def split_compound(text: str) -> list[str]:
    """Split the safe compound form: launch an app, then type a payload."""
    normalized = normalize(text)
    match = re.match(r"^(.*?)\s+(?:and|then|aur)\s+(.+)$", normalized)
    if not match:
        return [normalized]

    launch, remainder = match.groups()
    launch_intent = route(launch)
    if launch_intent.name != "open_app":
        return [normalized]

    payload = extract_text_payload(remainder)
    if payload == remainder:
        payload = re.sub(r"\s+(?:kholo|khol|likho|likh)\s*$", "", payload).strip()
    if not payload:
        return [normalized]
    return [launch, f"type {payload}"]


def _lean_table(table: dict) -> dict:
    """`table` re-keyed with the words that name nothing taken out.

    Built once per table and cached on the table's id. An ambiguous lean key
    - two different phrases that strip to the same words but mean different
    things - is dropped rather than resolved by dict order, because a wrong
    keystroke is worse than falling through to the next test.
    """
    cached = _LEAN_CACHE.get(id(table))
    if cached is not None:
        return cached

    lean: dict[str, str] = {}
    clashed: set[str] = set()
    for phrase, spec in table.items():
        words = [w for w in phrase.split() if w not in config.KEY_WRAPPERS]
        if not words:
            continue
        key = " ".join(words)
        if key in lean and lean[key] != spec:
            clashed.add(key)
        lean[key] = spec
    for key in clashed:
        lean.pop(key, None)

    _LEAN_CACHE[id(table)] = lean
    return lean


_LEAN_CACHE: dict[int, dict] = {}


def _longest_run(words: list[str], table: dict, exact: bool = False) -> str | None:
    """The longest run of `words` that is a whole phrase in `table`."""
    for size in range(len(words), 0, -1):
        for start in range(len(words) - size + 1):
            spec = table.get(" ".join(words[start:start + size]))
            if spec is None:
                continue
            outside = words[:start] + words[start + size:]
            if exact:
                if not outside:
                    return spec
            elif all(w in config.KEY_WRAPPERS for w in outside):
                return spec
    return None


def _match_phrase_table(text: str, table: dict) -> str | None:
    """The longest run of this sentence that is a whole phrase in `table`.

    This used to demand an exact match on the entire utterance, and that is
    why "Minimize kholo" went looking for an app called minimize and "then
    open new tab" for a file called new tab. One extra word and the whole
    table stopped applying - which is the worst way for it to fail, because
    the fallthrough is a disk search.

    It is still not a bare substring match. Two rules keep it safe:

      - the run has to be a COMPLETE phrase from the table, never part of one
      - every word left outside the run must be in config.KEY_WRAPPERS, and
        nothing in there names a target

    The second rule is the one carrying the weight. "desktop pe wapas jao"
    really does contain "wapas", and "wapas" really is a Left arrow in the
    table - but matching it leaves "desktop" outside the run, "desktop" names
    something, so the match is refused and the sentence goes on to open the
    folder. That was the trap the old exact match was avoiding, and this is
    how it stays avoided without throwing the rest of the table away.

    Longest run first, so "tab kholo" matches itself instead of being shaved
    down to "kholo", and "window band karo" wins over the "band karo" sitting
    inside it.

    THE SECOND PASS, and why it exists: the run above has to be CONTIGUOUS,
    so a word that names nothing breaks the phrase in half when it lands in
    the middle of one. "close the window" is the whole bug - "the" is already
    in KEY_WRAPPERS, so it is tolerated on either side of a match and not
    between the two words of one. The table has "close window"; the sentence
    says "close the window"; the match failed, and it went to LAYA, which
    spent 12 seconds deciding it was unknown.

    So the second pass asks the same question of the same table with those
    words removed from both sides. It is the existing rule applied in the
    one position it was not: a word that names nothing cannot change what a
    phrase means, wherever in the phrase it sits. And it is stricter than the
    first pass, not looser - the lean run must account for EVERY remaining
    word, so "music band kholo" still refuses to be "band" with "music" left
    over, which is the protection the outside-words rule was there to give.
    """
    words = [w for w in re.sub(r"[^\w\s]", " ", text).split() if w]
    if not words:
        return None

    spec = _longest_run(words, table)
    if spec is not None:
        return spec

    lean = [w for w in words if w not in config.KEY_WRAPPERS]
    if not lean or len(lean) == len(words):
        return None  # nothing was removed, so the second pass is the first one
    return _longest_run(lean, _lean_table(table), exact=True)


def _match_key_command(text: str) -> str | None:
    """The keystroke this sentence asks for, or None."""
    return _match_phrase_table(text, config.KEY_COMMANDS)


def _match_window_command(text: str) -> str | None:
    """The window action this sentence asks for, or None."""
    return _match_phrase_table(text, config.WINDOW_COMMANDS)


# ------------------------------------------------------------------ route
def route(text: str) -> Intent:
    text = normalize(text)
    if not text:
        return Intent("unknown", "any", "", "keywords")

    # Window management before the keystroke table, and the order matters:
    # "ye band karo" contains "band karo", which the keystroke table reads as
    # Escape, and "ye" is a wrapper word so that match would be accepted.
    # Asking the window table first gets the whole phrase instead.
    verb = _match_window_command(text)
    if verb:
        return Intent("window_action", "any", verb, "window")

    # Screen control goes next. It is a whole-phrase match, so it is both the
    # cheapest test here and among the hardest to misfire.
    spec = _match_key_command(text)
    if spec:
        return Intent("press_keys", "any", spec, "keys")

    # A combo named out loud: "alt f4", "ctrl shift n", "windows d". This
    # sits here, immediately after the phrase table, because the
    # fallthrough was actively harmful rather than just unhelpful - LAYA
    # labels an unparsed combo `open_app`, and open_app searches the disk,
    # so "ctrl t" used to launch CertEnrollCtrl.exe.
    spec = spoken_keys.parse(text)
    if spec:
        return Intent("press_keys", "any", spec, "spoken_keys")
    if spoken_keys.looks_like_attempt(text):
        # Meant as a combo, but not one that can be sent. The empty spec
        # makes actions.press_keys ask which control - the only safe reply.
        # What it must never do is reach the file search.
        return Intent("press_keys", "any", "", "spoken_keys")

    # "click on the second link". Sits after the keystroke tests, so "next"
    # is still a slide and "alt f4" is still a combo, and before the keyword
    # table, because the fallthrough was the same disk search that made
    # spoken_keys necessary - it went looking for a file called "second link".
    pointed = click_target.parse(text)
    if pointed:
        kind, what = pointed
        if kind == "nth":
            return Intent("click_nth", "any", str(what), "click_target")
        return Intent("click_named", "any", str(what), "click_target")
    if click_target.looks_like_attempt(text):
        # You said click but never said what. The empty term makes actions
        # ask which one. Letting this reach LAYA meant "click karo" became
        # open_app and went hunting for a file named "click".
        return Intent("click_nth", "any", "", "click_target")

    base_term = extract_term(text)
    laya_first = bool(os.environ.get("SPARROW_LAYA_FIRST"))

    kw_intent, kw_type = _classify_with_keywords(text, base_term)
    intent, file_type, source, confidence = kw_intent, kw_type, "keywords", 0.0

    # Only pay LAYA's ~1.2s when the keyword table has nothing to offer.
    if laya_first or kw_intent == "unknown":
        verdict = _classify_with_laya(text)
        if verdict:
            laya_intent, laya_type, conf, key_action = verdict

            # A phrasing the KEY_COMMANDS table never heard of, like "move
            # on to the next one". LAYA cannot extract a key, but picking
            # the direction out of a fixed list is exactly what it is for.
            if laya_intent == "press_keys":
                laya_spec = KEY_ACTION_SPECS.get(key_action)
                if laya_spec:
                    return Intent("press_keys", "any", laya_spec, "laya", conf)
                laya_intent = "unknown"  # it saw screen control but no direction

            # LAYA is non-autoregressive: every label competes with every
            # other, and with nothing better on offer the launch labels win.
            # "uh um" came back open_app at p>0.45 and opened a file out of a
            # cache folder. So a launch verdict from LAYA alone is not enough
            # - the sentence has to contain something that actually points at
            # a thing. The keyword table is exempt: it only ever fires on the
            # words themselves, so it carries its own evidence.
            if (laya_intent in ("open_app", "open_file", "open_folder")
                    and not _has_launch_signal(text, base_term)):
                laya_intent = "unknown"

            intent, source, confidence = laya_intent, "laya", conf
            # Trust the keyword file_type when it found one - LAYA is weak
            # here. Let LAYA fill in only when keywords saw no type hint.
            file_type = kw_type if kw_type != "any" else laya_type

    # FOLDER_ALIASES is an explicit list you maintain, so it overrides any
    # guess: "downloads kholo" is the folder, never a file named it.
    if intent in ("open_file", "open_app") and base_term in config.FOLDER_ALIASES:
        intent = "open_folder"

    # A question is never a launch. LAYA calls "what is the capital of
    # france" open_app at p=0.51, and open_app falls back to searching the
    # disk, so a question used to go hunting for a file named after it. The
    # web is both the right answer and a safe one.
    if intent in ("open_app", "open_file") and _looks_like_question(text):
        intent, source = "web_search", source + "+question"

    # Last resort, and the one place this bot could do something you never
    # asked for. A question wants the web, which is harmless. Anything else
    # unrecognised must NOT turn into a disk search: that is how a half-heard
    # fragment used to launch a random executable out of System32.
    #
    # The single exception is a name you taught it in config.NAME_ALIASES.
    # "food4all" on its own is a thing you own, not a guess - and if you
    # want another bare noun to work this way, add it there.
    if intent == "unknown" and base_term:
        if _looks_like_question(text):
            intent, source = "web_search", source + "+default"
        elif base_term.lower() in _KNOWN_NAMES:
            intent, source = "open_file", source + "+known-name"
        elif spoken_keys.names_a_modifier(text):
            # "ctrl something". A modifier was said, so this was aimed at the
            # keyboard - the empty spec makes actions ask which key instead of
            # going anywhere near the disk. Safe to test only here: anything
            # with a real launch word in it never reaches this branch, so
            # "control panel kholo" is still the app.
            intent, source = "press_keys", source + "+modifier"
        # else: stays unknown, and actions asks instead of guessing.

    # The browser name is how you said where to search, not what to search
    # for. Only a browser is dropped - "google karo spotify alternatives"
    # has to keep "spotify".
    target = ""
    if intent == "type_text":
        # Target and payload come out together: "type on notepad hello" has
        # to split into the window and the line, or the window's name gets
        # typed into it.
        target, term = extract_type_target(text)
    elif intent == "close_app":
        # The exe, not the spoken words: "close command prompt" has to become
        # "cmd" for anything to be found. Falls back to the raw term so an
        # app that was never aliased still names itself.
        term = match_app(text) or base_term
    elif intent == "web_search":
        term = extract_query(text, drop=_match_browser_name(text))
    elif intent == "press_keys":
        # Only reachable from the +modifier branch above: every combo that
        # actually parsed returned long ago with its spec already set. So
        # there is no key here, and an empty spec is what makes actions ask
        # which one rather than pressing a transcript fragment.
        term = ""
    elif intent == "youtube_channel":
        # Drop the word "youtube" itself; what is left is the channel. Whole
        # words again - a plain replace() took the "yt" out of the middle of
        # "python" and handed back "phon".
        term = base_term
        for yw in _YOUTUBE_WORDS:
            term = re.sub(rf"\b{yw}\b", " ", term)
        term = " ".join(term.split())
    else:
        term = base_term
    return Intent(intent, file_type, term, source, confidence, target)
