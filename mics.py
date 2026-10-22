"""Which microphone is actually hearing you?

    python mics.py          # list every input device
    python mics.py 3        # record 3 seconds from device 3 and show the level

The level is what matters. Speak during the recording: anything above about
0.01 is a working mic, 0.000 means that device hears nothing. Put the
winning number (or a piece of its name) into SPARROW_MIC, or into
MIC_DEVICE in config.py.
"""

from __future__ import annotations

import sys

import numpy as np
import sounddevice as sd

import config


def listing() -> None:
    default = sd.default.device[0]
    print(f"{'#':>3}  {'ch':>2}  device")
    print("-" * 60)
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] < 1:
            continue
        mark = " <- windows default" if index == default else ""
        print(f"{index:>3}  {device['max_input_channels']:>2}  "
              f"{device['name']}{mark}")
    print("\nTest one with:  python mics.py <number>")


def level_test(index: int, seconds: float = 3.0) -> None:
    name = sd.query_devices(index)["name"]
    print(f"Recording {seconds:.0f}s from [{index}] {name}")
    print("SPEAK NOW...")
    audio = sd.rec(
        int(seconds * config.SAMPLE_RATE),
        samplerate=config.SAMPLE_RATE,
        channels=1,
        dtype="float32",
        device=index,
    )
    sd.wait()

    flat = audio.flatten()
    rms = float(np.sqrt(np.mean(np.square(flat))))
    peak = float(np.max(np.abs(flat)))
    print(f"\n  rms  {rms:.4f}   (silence threshold is {config.SILENCE_RMS})")
    print(f"  peak {peak:.4f}")
    if rms < config.SILENCE_RMS:
        print("  -> this device heard nothing. Try another number.")
    else:
        print(f"  -> this one works. Use it:  set SPARROW_MIC={index}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        level_test(int(sys.argv[1]))
    else:
        listing()
