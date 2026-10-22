"""Typed, allow-listed action contract shared by routing and execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar


ACTION_NAMES = frozenset({
    "open_app",
    "open_file",
    "open_folder",
    "web_search",
    "youtube_channel",
    "press_keys",
    "type_text",
    "unknown",
})


@dataclass(frozen=True)
class Action:
    """A validated action; parameters are extracted locally, never generated."""

    name: str
    parameters: dict[str, str] = field(default_factory=dict)
    source: str = "keywords"
    confidence: float = 0.0

    _REQUIRED: ClassVar[dict[str, tuple[str, ...]]] = {
        "open_app": ("app",),
        "open_file": ("term",),
        "open_folder": ("term",),
        "web_search": ("term",),
        "youtube_channel": ("term",),
        "press_keys": ("spec",),
        "type_text": ("text",),
        "unknown": (),
    }

    def __post_init__(self) -> None:
        if self.name not in ACTION_NAMES:
            raise ValueError(f"unsupported action: {self.name}")
        missing = [key for key in self._REQUIRED[self.name]
                   if not self.parameters.get(key)]
        if missing:
            raise ValueError(f"{self.name} requires {', '.join(missing)}")

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.name,
            "parameters": dict(self.parameters),
            "source": self.source,
            "confidence": self.confidence,
        }
