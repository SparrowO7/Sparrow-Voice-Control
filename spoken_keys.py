"""Say a key combo out loud: "alt f4", "ctrl shift n", "windows d".

This exists because the alternative was dangerous, not merely missing. An
unlisted phrase falls through to LAYA, which calls almost anything
`open_app`, and open_app then searches the disk. Measured before this file
existed:

    "ctrl t"   ->  launched C:\\Windows\\System32\\CertEnrollCtrl.exe
    "alt f 4"  ->  opened a random PDF out of Downloads
    "alt f4"   ->  "no app matched"

So saying a keystroke could start a program you never asked for. Two things
fix that: parse the combo properly, and when it *looks* like a combo but
will not parse, say so instead of guessing.

config.KEY_COMMANDS stays the place for phrasings ("next slide"). This is
only for naming the keys themselves.
"""

from __future__ import annotations

import re

# Whisper writes these a few different ways. "window" is deliberately NOT
# here: "window band karo" is a real command and must not become Win+...
_MOD_WORDS = {
    "ctrl": "ctrl", "control": "ctrl", "cntrl": "ctrl", "cntl": "ctrl",
    "ctl": "ctrl", "kontrol": "ctrl",
    "alt": "alt", "alter": "alt",
    "shift": "shift", "sift": "shift",
    "win": "win", "windows": "win", "super": "win", "meta": "win",
    "cmd": "win", "command": "win",
}

# Named keys, spoken -> what keys.press understands.
_KEY_WORDS = {
    "tab": "tab", "enter": "enter", "return": "enter",
    "escape": "esc", "esc": "esc",
    "space": "space", "spacebar": "space",
    "backspace": "backspace", "back space": "backspace",
    "delete": "delete", "del": "delete",
    "insert": "insert", "ins": "insert",
    "home": "home", "end": "end",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "pageup": "page_up", "pagedown": "page_down",
    "plus": "=", "equals": "=", "equal": "=", "minus": "-", "dash": "-",
}

_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "to": "2", "too": "2", "three": "3",
    "four": "4", "for": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "ate": "8", "nine": "9", "ten": "10", "eleven": "11",
    "twelve": "12",
}

# Dropped from either end before parsing: "press alt f4 karo".
_FILLERS = {
    "press", "pressed", "hit", "push", "dabao", "daba", "dabana", "maro",
    "key", "keys", "button", "combo", "karo", "kar", "kardo", "do", "please",
    "jarvis", "sparrow", "hey", "ok", "okay", "ab", "now", "zara", "bhai",
}

# Two-word forms collapsed before tokenising, so "page down" survives as one
# key and does not look like the key "down" with a stray word in front.
_PHRASES = {
    "page down": "pagedown", "page up": "pageup",
    "back space": "backspace",
    "function": "f", "fn": "f",
}


def _collapse(text: str) -> str:
    for spoken, single in _PHRASES.items():
        text = re.sub(rf"\b{spoken}\b", single, text)
    return text


def _tokens(text: str) -> list[str]:
    text = _collapse(re.sub(r"[^\w\s]", " ", (text or "").lower()))
    words = [w for w in text.split() if w]
    while words and words[0] in _FILLERS:
        words.pop(0)
    while words and words[-1] in _FILLERS:
        words.pop()
    return words


def _as_key(token: str, nxt: str | None) -> tuple[str, int] | None:
    """Resolve one main key. Returns (key, tokens_consumed) or None.

    The two-token case is "f" standing alone, because Whisper splits F-keys
    as often as not: "alt f 4" and "alt f four" both arrive that way.
    """
    if token in _KEY_WORDS:
        return _KEY_WORDS[token], 1

    # "f4" / "f12" in one piece.
    match = re.fullmatch(r"f(\d{1,2})", token)
    if match and 1 <= int(match.group(1)) <= 24:
        return f"f{int(match.group(1))}", 1

    # "f" + number, as two tokens.
    if token == "f" and nxt:
        digit = nxt if nxt.isdigit() else _NUMBER_WORDS.get(nxt)
        if digit and 1 <= int(digit) <= 24:
            return f"f{int(digit)}", 2

    if len(token) == 1 and (token.isalpha() or token.isdigit()):
        return token, 1
    if token in _NUMBER_WORDS and len(_NUMBER_WORDS[token]) == 1:
        return _NUMBER_WORDS[token], 1
    return None


def _is_explicit(text: str) -> bool:
    """Did they actually say "press ..."? That removes the ambiguity."""
    words = re.sub(r"[^\w\s]", " ", (text or "").lower()).split()
    return bool(words) and words[0] in ("press", "dabao", "hit", "push")


def parse(text: str) -> str | None:
    """"alt f4" -> "alt+f4". None when this is not a key combo at all.

    A bare key with no modifier is refused unless you said "press" first.
    "delete" on its own is far more likely to be a half-heard sentence than
    a deliberate keystroke, and config.KEY_COMMANDS already covers the bare
    keys worth saying. "press delete" is unambiguous, so that one goes
    through.
    """
    words = _tokens(text)
    if not words:
        return None

    mods: list[str] = []
    index = 0
    while index < len(words) and words[index] in _MOD_WORDS:
        spec = _MOD_WORDS[words[index]]
        if spec not in mods:
            mods.append(spec)
        index += 1

    rest = words[index:]
    if not rest:
        return None  # modifiers only - "ctrl" alone presses nothing

    nxt = rest[1] if len(rest) > 1 else None
    resolved = _as_key(rest[0], nxt)
    if resolved is None:
        return None

    key, used = resolved
    if len(rest) > used:
        return None  # trailing words mean this was a sentence, not a combo

    if not mods:
        # A lone F-key is unambiguous and genuinely spoken, so it needs no
        # "press". Anything else bare does.
        if re.fullmatch(r"f\d{1,2}", key) or _is_explicit(text):
            return key
        return None

    # Deterministic order, so "shift ctrl n" and "ctrl shift n" agree.
    order = {"ctrl": 0, "alt": 1, "shift": 2, "win": 3}
    mods.sort(key=lambda m: order.get(m, 9))
    return "+".join(mods + [key])


def looks_like_attempt(text: str) -> bool:
    """Was this meant to be a key combo, even though it would not parse?

    Kept tight, because a false positive here silences a real command.
    "control panel kholo" starts with a modifier word and must still open
    the Control Panel, so every remaining token has to look like part of a
    combo - "panel" does not.
    """
    words = _tokens(text)
    if not words or len(words) > 5:
        return False

    # An explicit "press ..." is an attempt whatever follows it.
    if _is_explicit(text):
        return True

    if words[0] not in _MOD_WORDS:
        return False
    return all(
        word in _MOD_WORDS
        or word in _KEY_WORDS
        or word in _NUMBER_WORDS
        or word == "f"
        or re.fullmatch(r"f?\d{1,2}", word)
        or len(word) == 1
        for word in words[1:]
    )


def names_a_modifier(text: str) -> bool:
    """Does this open with a modifier key, whatever follows it?

    Far looser than looks_like_attempt, and only safe to ask once the router
    has already ruled out every other reading - "control panel kholo" would
    answer True here, so this must never be consulted before the launch
    words have had their turn. See the tail of router.route().
    """
    words = _tokens(text)
    return bool(words) and words[0] in _MOD_WORDS
