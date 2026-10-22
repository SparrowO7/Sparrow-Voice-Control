import listen
import config

# Test key normalisation
from pynput import keyboard

print("Press Ctrl+Alt and release. Watch the output:")
print("Expected: ctrl -> True, alt -> True")

down = set()
KEY_NAMES = {
    "ctrl": {"ctrl", "ctrl_l", "ctrl_r"},
    "alt": {"alt", "alt_l", "alt_r", "alt_gr"},
    "shift": {"shift", "shift_l", "shift_r"},
}

def _normalise(key):
    name = getattr(key, "name", None) or getattr(key, "char", None)
    if not name:
        return None
    for canonical, variants in KEY_NAMES.items():
        if name in variants:
            return canonical
    return name

def on_press(key):
    name = _normalise(key)
    if name:
        down.add(name)
        print(f"  pressed: {name} -> down={down}")
        if config.HOTKEY <= down:
            print(f"  *** HOTKEY TRIGGERED! ***")

def on_release(key):
    name = _normalise(key)
    if name:
        down.discard(name)
        print(f"  released: {name} -> down={down}")

with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
    listener.join()