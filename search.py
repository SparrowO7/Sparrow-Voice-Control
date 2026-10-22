"""File lookup. Uses Everything's es.exe when present, else a cached walk.

Both paths return the same thing: a ranked list of Paths, best guess first.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import config

_INDEX_FILE = config.CACHE / "file_index.json"
_INDEX_TTL = 6 * 3600  # rebuild the fallback index twice a day at most


def es_path() -> Path | None:
    """Locate Everything's CLI, or None if it isn't installed yet."""
    for candidate in config.ES_EXE_CANDIDATES:
        if candidate.is_file():
            return candidate
    from shutil import which

    found = which("es")
    return Path(found) if found else None


def _is_junk(path: Path) -> bool:
    """Everything sees the whole drive, so screen out the noise.

    The dotted-parent test is the important one, and it is new because of
    what it caught: "uh um" resolved to a file under .cache\\codex-runtimes\\
    and "harsh tiwari portfolio" to one under .gemini\\antigravity\\. Both
    files were real, so nothing upstream rejected them. A leading dot is the
    convention for "a program's private data", and you do not ask for those
    out loud - naming every such folder as it appears would be a losing game,
    there is a new one every month.
    """
    lowered = str(path).lower()
    if any(part in lowered for part in config.EXCLUDE_PATH_PARTS):
        return True
    if any(part.startswith(".") for part in path.parts[:-1]):
        return True
    return path.name.startswith(config.EXCLUDE_NAME_PREFIXES)


# ------------------------------------------------------------------ everything
def _search_everything(es: Path, term: str, exts: list[str], limit: int) -> list[Path]:
    # Separate argv entries, not one spaced string: Everything ANDs distinct
    # terms but treats "food4all architecture" as a literal phrase.
    query = term.split()
    if exts:
        # es.exe wants ext:docx;doc;md  (no dots)
        query.append("ext:" + ";".join(e.lstrip(".") for e in exts))

    # Ask for plenty - the junk filter throws most of it away.
    cmd = [str(es), "-n", str(limit * 10), "file:", *query]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (subprocess.TimeoutExpired, OSError):
        return []

    if out.returncode != 0:
        return []

    results = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if line:
            results.append(Path(line))
    return results


# ------------------------------------------------------------------ fallback
def _walk_roots() -> list[str]:
    paths: list[str] = []
    for root in config.SEARCH_ROOTS:
        if not root.is_dir():
            continue
        root_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            here = Path(dirpath)
            if len(here.parts) - root_depth >= config.FALLBACK_INDEX_MAX_DEPTH:
                dirnames.clear()
                continue
            dirnames[:] = [
                d for d in dirnames
                if d not in config.SKIP_DIRS and not d.startswith(".")
            ]
            for name in filenames:
                paths.append(str(here / name))
    return paths


def _load_index(force_rebuild: bool = False) -> list[str]:
    if not force_rebuild and _INDEX_FILE.is_file():
        try:
            blob = json.loads(_INDEX_FILE.read_text(encoding="utf-8"))
            if time.time() - blob.get("built_at", 0) < _INDEX_TTL:
                return blob["paths"]
        except (json.JSONDecodeError, KeyError, OSError):
            pass

    paths = _walk_roots()
    config.CACHE.mkdir(parents=True, exist_ok=True)
    _INDEX_FILE.write_text(
        json.dumps({"built_at": time.time(), "paths": paths}),
        encoding="utf-8",
    )
    return paths


def _search_index(term: str, exts: list[str], limit: int) -> list[Path]:
    tokens = term.lower().split()
    hits = []
    for raw in _load_index():
        name = os.path.basename(raw).lower()
        if not all(tok in name for tok in tokens):
            continue
        if exts and not any(name.endswith(e) for e in exts):
            continue
        hits.append(Path(raw))
        if len(hits) > limit * 5:
            break
    return hits


# Words too common to be a search on their own. Every laptop has hundreds
# of files called "document" or "notes", and Everything returns them in no
# meaningful order, so a query of one of these alone is worse than nothing:
# it opens a confident wrong answer instead of saying it found nothing.
_GENERIC_TERMS = {
    "document", "documents", "doc", "docs", "file", "files", "note", "notes",
    "report", "reports", "sheet", "sheets", "image", "images", "photo",
    "photos", "picture", "pictures", "video", "videos", "data", "text",
    "final", "new", "old", "copy", "untitled", "draft", "temp", "test",
    "screenshot", "download", "downloads", "presentation", "slides",
}


def _token_weight(token: str) -> float:
    """How much this word narrows a file search.

    Length is a decent proxy. A digit mixed in with letters is a strong
    one - that pattern is a project or version identifier ("food4all",
    "phase1", "v2") and almost nothing else on the disk shares it.

    A bare number gets no bonus: "2026" is a year, and a year appears in
    half the filenames on a laptop. Weighting it up made "my expenses
    sheet 2026" narrow to "2026" instead of "expenses".
    """
    if token in _GENERIC_TERMS:
        return 0.0
    weight = float(len(token))
    has_digit = any(ch.isdigit() for ch in token)
    if has_digit and any(ch.isalpha() for ch in token):
        weight += 6.0
    return weight


def _term_variants(term: str) -> list[str]:
    """Progressively looser attempts, so a stray word can't kill the search.

    Drops the *least* informative word each round, not the last one. That
    distinction is the whole point: "ruffin food4all document" used to
    narrow to "ruffin" (a Whisper mishearing) and then fall back to
    "document" alone, which opened a random Chrome extension's
    `document.js`. It now narrows to "food4all", which is the only word in
    the sentence that identifies anything.

    "food4all architecture notes" -> that, then "food4all architecture",
    then "food4all". First one that hits wins.
    """
    tokens = term.split()
    if len(tokens) <= 1:
        return [term] if term else []

    variants = [" ".join(tokens)]
    remaining = list(tokens)
    while len(remaining) > 1:
        # On a tie, drop the rightmost: in a spoken command the subject
        # tends to come first and the qualifiers trail after it.
        weakest = min(
            range(len(remaining)),
            key=lambda i: (_token_weight(remaining[i]), -i),
        )
        remaining.pop(weakest)
        variants.append(" ".join(remaining))

    return [v for v in variants if " " in v or _token_weight(v) > 0]


# ------------------------------------------------------------------ ranking
def _score(path: Path, term: str, exts: list[str]) -> float:
    stem = path.stem.lower()
    flat = stem.replace(" ", "").replace("_", "").replace("-", "")
    tokens = [t for t in term.lower().split() if t]
    needle = "".join(tokens)

    score = 0.0
    if flat == needle:
        score += 100
    elif flat.startswith(needle):
        score += 60
    elif needle in flat:
        score += 30
    else:
        # Multi-word match: reward each token that landed somewhere in the name.
        score += 20 * sum(1 for tok in tokens if tok in flat) / max(len(tokens), 1)

    # Honour the spoken file type: "the doc" should beat the pdf next to it.
    if exts:
        suffix = path.suffix.lower()
        if suffix in exts:
            score += 40 - exts.index(suffix) * 3

    # Shallower paths are usually the real thing, not a backup copy.
    score -= len(path.parts) * 1.5

    lowered = str(path).lower()
    if "temp" in lowered or "appdata" in lowered:
        score -= 50
    if "onedrive" in lowered:
        score += 3  # synced folder is usually the live copy

    try:
        age_days = (time.time() - path.stat().st_mtime) / 86400
        score += max(0.0, 15 - age_days / 30)
    except OSError:
        pass

    return score


def find_files(term: str, file_type: str = "any", limit: int = 8) -> list[Path]:
    """Ranked matches for a spoken search term."""
    if not term:
        return []

    exts = config.FILE_TYPES.get(file_type, [])
    es = es_path()

    unique: list[Path] = []
    matched = term
    for variant in _term_variants(term):
        raw = (
            _search_everything(es, variant, exts, limit)
            if es
            else _search_index(variant, exts, limit)
        )

        seen: set[str] = set()
        unique = []
        for path in raw:
            key = str(path).lower()
            if key in seen or _is_junk(path):
                continue
            seen.add(key)
            unique.append(path)

        # Only settle once something real survived the junk filter.
        if unique:
            matched = variant
            break

    unique.sort(key=lambda p: _score(p, matched, exts), reverse=True)
    return unique[:limit]


def find_folder(term: str, limit: int = 5) -> list[Path]:
    """Folders only - for 'open the food4all folder'."""
    if not term:
        return []

    es = es_path()
    if es:
        cmd = [str(es), "-n", str(limit * 3), "folder:", term]
        try:
            out = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            found = [Path(l.strip()) for l in out.stdout.splitlines() if l.strip()]
        except (subprocess.TimeoutExpired, OSError):
            found = []
    else:
        needle = term.lower()
        found = []
        for raw in _load_index():
            parent = Path(raw).parent
            if needle in parent.name.lower():
                found.append(parent)

    seen, unique = set(), []
    for path in found:
        key = str(path).lower()
        if key not in seen and path.is_dir() and not _is_junk(path):
            seen.add(key)
            unique.append(path)

    unique.sort(key=lambda p: _score(p, term, []), reverse=True)
    return unique[:limit]


def rebuild_index() -> int:
    """Force a fresh fallback index. Returns how many files were seen."""
    return len(_load_index(force_rebuild=True))


# Folders find_executable must still refuse, even though it waives the
# normal junk filter to reach Program Files and the Start Menu.
#
# "recent" is the one that bites, and there is more than one of them:
# Windows keeps `AppData\Roaming\Microsoft\Windows\Recent`, Office keeps
# `...\Microsoft\Office\Recent`, and both fill up with a .lnk for every
# document you have ever touched. "open food4all" ranked one of those
# first - a history breadcrumb, not a program. Matching the bare segment
# catches every variant. Nothing you would want to launch lives in a
# folder called Recent.
_EXE_JUNK_DIRS = (
    "\\recent\\",
    "\\recent items\\",
    "\\$recycle.bin\\",
    "\\package cache\\",
    "\\packages\\",
    "\\winsxs\\",
    "\\installer\\",
)


def find_executable(name: str, limit: int = 12) -> list[Path]:
    """Hunt for an app's .exe or Start Menu .lnk.

    Deliberately skips _is_junk: programs live in Program Files and their
    shortcuts live under AppData, both of which the normal filter drops.
    That concession has to be narrowed by hand, though - see _EXE_JUNK_DIRS.
    """
    if not name:
        return []

    es = es_path()
    if not es:
        return []

    cmd = [str(es), "-n", str(limit * 4), "file:", *name.split(), "ext:exe;lnk"]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (subprocess.TimeoutExpired, OSError):
        return []

    noise = ("uninstall", "unins000", "setup", "installer", "crashhandler",
             "updater", "update.exe", "repair", "helper", "report")
    hits = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        path = Path(line)
        lowered = str(path).lower()
        if any(part in lowered for part in _EXE_JUNK_DIRS):
            continue
        if any(word in path.name.lower() for word in noise):
            continue
        hits.append(path)

    def rank(path: Path) -> float:
        score = 0.0
        if path.stem.lower() == name.lower():
            score += 100
        elif path.stem.lower().startswith(name.lower()):
            score += 50
        if path.suffix.lower() == ".lnk":
            score += 30  # a Start Menu shortcut is the blessed entry point
        lowered = str(path).lower()
        if "start menu" in lowered:
            score += 25
        if "program files" in lowered:
            score += 10
        return score - len(path.parts)

    hits.sort(key=rank, reverse=True)
    return hits[:limit]
