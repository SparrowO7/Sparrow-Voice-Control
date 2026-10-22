"""Hear "click on the second link" and work out which thing you meant.

Separate file for the same reason spoken_keys.py is: anything this does not
catch falls through to the keyword table and then to LAYA, which labels
almost any leftover `open_app` - and open_app searches your disk. So
"click on the second link" would have gone looking for a *file* called
"second link". Parsing it here is what stops that.

Two shapes come out:

    ("nth", 2)        "click on the second link", "doosre link pe click karo"
    ("named", "sign in")   "click on the sign in button"

and None, which means this was never a click at all. None is the common
case, so the tests below are deliberately strict: a false positive here
hijacks a command that used to work.
"""

from __future__ import annotations

import re

# -1 is "the last one". Hinglish spellings included because Whisper picks
# whichever it feels like on the day.
_ORDINALS = {
    "first": 1, "1st": 1, "one": 1, "pehla": 1, "pehle": 1, "pahla": 1,
    "pehli": 1, "top": 1,
    "second": 2, "2nd": 2, "doosra": 2, "dusra": 2, "doosre": 2, "dusre": 2,
    "doosri": 2, "dusri": 2,
    "third": 3, "3rd": 3, "teesra": 3, "tisra": 3, "teesre": 3, "tisre": 3,
    "fourth": 4, "4th": 4, "chautha": 4, "chauthe": 4,
    "fifth": 5, "5th": 5, "paanchva": 5, "panchva": 5,
    "sixth": 6, "6th": 6, "chhatha": 6, "chhathe": 6,
    "seventh": 7, "7th": 7, "saatva": 7,
    "eighth": 8, "8th": 8, "aathva": 8,
    "ninth": 9, "9th": 9, "nauva": 9,
    "tenth": 10, "10th": 10, "dasva": 10,
    "last": -1, "aakhri": -1, "aakhiri": -1, "akhri": -1, "final": -1,
}

# What you can be pointing at. "tab" is deliberately absent: "naya tab" is
# Ctrl+T and must stay that way.
_TARGET_NOUNS = {
    "link", "links", "result", "results", "button", "buttons",
    "option", "options", "item", "items", "entry", "choice",
}

# A verb that only ever means "activate the thing on screen". "press" and
# "open" are NOT here - "press escape" is a keystroke and "open chrome" is
# an app, and stealing either would break a command that works today.
_CLICK_VERBS = {
    "click", "clicked", "tap", "tapp", "dabao", "daba", "dabaa",
    "select", "chuno", "choose",
}

# Dropped from either end before parsing, so "sparrow click the second link
# please" parses the same as "click second link".
_FILLERS = {
    "jarvis", "sparrow", "please", "hey", "ok", "okay", "ab", "now", "zara", "bhai",
    "karo", "kar", "kardo", "do", "the", "a", "an", "on", "onto", "upon",
    "pe", "par", "pr", "wale", "wala", "wali", "ko", "ka", "ki", "then",
    "phir", "fir", "aur", "and", "me", "mein",
}


def _tokens(text: str) -> list[str]:
    words = re.sub(r"[^\w\s]", " ", (text or "").lower()).split()
    while words and words[0] in _FILLERS:
        words.pop(0)
    while words and words[-1] in _FILLERS:
        words.pop()
    return words


def _ordinal_in(words: list[str]) -> int | None:
    """The position asked for, or None. Bare digits count too: "link 3"."""
    for word in words:
        if word in _ORDINALS:
            return _ORDINALS[word]
        # "2nd" is in the table; a bare "2" or "12" is also meant as one.
        if word.isdigit() and 1 <= int(word) <= 25:
            return int(word)
    return None


def parse(text: str) -> tuple[str, object] | None:
    """("nth", n) or ("named", label), else None.

    The gate is deliberately narrow. Either you used a word that can only
    mean clicking, or you named a position *and* what kind of thing it is.
    Anything looser swallowed real commands when I tried it.
    """
    words = _tokens(text)
    if not words:
        return None

    has_verb = any(word in _CLICK_VERBS for word in words)
    nouns = [word for word in words if word in _TARGET_NOUNS]
    position = _ordinal_in(words)

    if not has_verb and not (position is not None and nouns):
        return None

    if position is not None:
        return ("nth", position)

    # No position, so the rest of the sentence is the label: drop the verb
    # and the noun, then shave the fillers off what is left. Stripping only
    # at the ends of the whole utterance is not enough - "click on the sign
    # in button" leaves "on the" sitting in the middle of the label.
    label_words = [
        word for word in words
        if word not in _CLICK_VERBS and word not in _TARGET_NOUNS
    ]
    while label_words and label_words[0] in _FILLERS:
        label_words.pop(0)
    while label_words and label_words[-1] in _FILLERS:
        label_words.pop()

    label = " ".join(label_words).strip()
    if not label:
        # "click karo" with nothing else. Clicking *something* unnamed is
        # a guess, and guessing where to click is how you lose work.
        return None
    return ("named", label)


def looks_like_attempt(text: str) -> bool:
    """A click you could not resolve, so the reply should say so.

    Only true when a click verb was actually used. Without that it is far
    more likely to be an ordinary command that simply mentioned a number.
    """
    words = _tokens(text)
    return bool(words) and any(word in _CLICK_VERBS for word in words)
