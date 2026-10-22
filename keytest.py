"""Which key do your earbuds actually send?

    python keytest.py

Press keys, tap your earbud button, and it prints the name of whatever
arrives. The name it prints is exactly what goes into SPARROW_TAP_KEY or
TAP_KEY in config.py. Ctrl+C to stop.

Most earbuds send media_play_pause on a single tap, but plenty send
media_next or nothing at all - a tap handled entirely inside the earbuds'
own firmware never reaches Windows, and then the earbud trigger cannot
work. This tells you which case you are in, in about ten seconds.
"""

from __future__ import annotations

import config
from listen import _normalise


def main() -> None:
    from pynput import keyboard

    print("Press any key, or tap your earbud button.")
    print(f"config.TAP_KEY is currently '{config.TAP_KEY}'")
    print("Ctrl+C to stop.\n")

    seen: set[str] = set()

    def on_press(key) -> None:
        name = _normalise(key)
        if name is None:
            print(f"  (unnamed key: {key!r})")
            return
        flag = "  <- this is your TAP_KEY" if name.startswith("media_") else ""
        first = "" if name in seen else "  [new]"
        seen.add(name)
        print(f"  {name}{flag}{first}")

    try:
        with keyboard.Listener(on_press=on_press) as listener:
            listener.join()
    except KeyboardInterrupt:
        pass

    media = sorted(n for n in seen if n.startswith("media_"))
    print("\n" + "-" * 50)
    if media:
        print("Media keys Windows received:")
        for name in media:
            print(f"  set SPARROW_TAP_KEY={name}")
    else:
        print("No media key arrived. Either you did not tap, or your earbuds")
        print("handle the tap internally and never tell Windows. In that case")
        print("use the laptop hotkey instead:  set SPARROW_TRIGGER=hold")


if __name__ == "__main__":
    main()
