# Sparrow — Offline Desktop Voice Control

Control a Windows desktop with English and Hinglish voice commands. Sparrow runs speech recognition locally, turns a command into a structured action, and opens apps, finds files, types text, or controls the active window.

Built by [Harsh Tiwari / Sparrow07](https://sparrow07.me/).

## What it does

- **Local speech recognition:** faster-whisper transcribes microphone audio on the computer.
- **English and Hinglish commands:** keyword rules and an optional LAYA classifier route commands to supported actions.
- **App, file and folder launching:** Everything search is supported, with a built-in file index as a fallback.
- **Desktop controls:** presentation navigation, media keys, scrolling, window controls and targeted text entry.
- **Voice triggers:** hold Ctrl+Alt or tap a supported earbud/media key. The default enables both.
- **Spoken feedback:** Windows text-to-speech replies report the result.
- **Optional browser actions:** web search and accessible link/button selection. Internet features need an internet connection.

This is a Windows desktop project, so there is no browser-hosted live demo. The source and setup instructions are provided here.

## How it works

```text
Keyboard hold / earbud media-key tap
                 |
          Microphone recording
                 |
        faster-whisper (local STT)
                 |
    Keyword rules + optional LAYA router
                 |
       Structured desktop action
                 |
   Windows / Everything / active application
                 |
         Spoken result and log
```

Keyword matching handles familiar phrases first. LAYA is used as a fallback for less familiar wording; Python extracts the target app, search term or file type. LAYA classifies commands rather than generating shell commands.

## Setup

Use a Windows machine with Python 3.10 or newer, a microphone and an available audio output. Package and GPU compatibility depend on the installed Python and CUDA versions.

```powershell
git clone https://github.com/SparrowO7/Sparrow-Voice-Control.git
cd Sparrow-Voice-Control
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Download the speech and optional router models once while connected to the internet:

```powershell
.\.venv\Scripts\python.exe warmup.py
```

Once the models are cached, local desktop commands can run offline. Web searches, online pages and first-time model downloads still need connectivity. LAYA falls back to keyword routing if it is unavailable.

For faster local file lookup, optionally install [Everything](https://www.voidtools.com/) and its ES command-line client. Place `es.exe` beside `main.py` or on PATH. The binary is not redistributed in this repository. Without it, Sparrow searches its cached index of standard user folders.

## Run

```powershell
.\.venv\Scripts\python.exe main.py                  # voice mode
.\.venv\Scripts\python.exe main.py --text           # typed commands
.\.venv\Scripts\python.exe main.py --text --dry     # inspect routing without performing the action
```

After activating the virtual environment, `Sparrow.bat` is also a launcher. Close the console or press Ctrl+C to stop Sparrow.

With keyboard hold, hold **Ctrl+Alt**, speak, then release. With a supported earbud/media-key tap, tap once and speak; recording ends after a short silence. Earbuds that do not send media-key events to Windows need the keyboard trigger.

## Example commands

| Say | Action |
| --- | --- |
| `chrome kholo` | Open Chrome |
| `downloads kholo` | Open Downloads |
| `project ka pdf kholo` | Search for a matching PDF |
| `open notepad and write hello world` | Open Notepad and type the payload |
| `agla slide` | Advance a presentation |
| `previous slide` | Go back one slide |
| `click on the second link` | Select a result in a supported browser/window |
| `google karo python documentation` | Search the web |

Text and key actions depend on the focused application. Sparrow includes console guards and confirmation handling for certain window and system actions, but recognition and application focus still need user attention.

## Configuration

Settings live in `config.py`; common overrides use environment variables:

| Variable | Purpose |
| --- | --- |
| `SPARROW_TRIGGER=hold` | Use Ctrl+Alt keyboard hold |
| `SPARROW_TRIGGER=tap` | Use the configured media-key tap |
| `SPARROW_TAP_KEY=media_play_pause` | Choose the media-key trigger |
| `SPARROW_MIC` | Choose a microphone index or name fragment |
| `SPARROW_DEVICE=cpu` | Force CPU transcription |
| `SPARROW_WHISPER=base` | Select a smaller speech model |
| `SPARROW_NO_LAYA=1` | Use keyword routing only |
| `SPARROW_KEEP_CLIPS=0` | Disable stored diagnostic audio clips |

For example, in PowerShell:

```powershell
$env:SPARROW_TRIGGER = "hold"
$env:SPARROW_DEVICE = "cpu"
$env:SPARROW_KEEP_CLIPS = "0"
.\.venv\Scripts\python.exe main.py
```

`APP_ALIASES`, `FOLDER_ALIASES` and `FILE_TYPES` customize applications, folder names and file matching. `python mics.py` lists audio inputs; `python keytest.py` helps identify the key sent by an earbud.

## Local data

Runtime data stays under `cache/`, including downloaded speech models, file indexes, logs, optional audio clips and the optional dedicated browser profile. The default retains up to 20 diagnostic clips; set `SPARROW_KEEP_CLIPS=0` to disable retention. These files, executables and local environment files are excluded by `.gitignore` and are not included in this source release.

## Source map

| File | Responsibility |
| --- | --- |
| `main.py` | Voice/text entry points and command orchestration |
| `listen.py`, `speak.py` | Audio capture, local transcription and speech replies |
| `router.py`, `action_schema.py` | Command routing and structured actions |
| `spoken_keys.py`, `click_target.py` | Spoken key combinations and click-target parsing |
| `actions.py`, `keys.py`, `state.py` | Desktop actions, key input and window state |
| `search.py` | Everything integration and fallback file search |
| `screen.py`, `browser.py` | Accessibility and browser control helpers |
| `config.py` | Configuration and command vocabulary |
| `warmup.py` | One-time model preparation |
| `test_*.py`, diagnostics | Routing checks and desktop diagnostic scripts |

## Checks and limitations

Python syntax and keyword command routing can be checked without starting microphone capture or controlling the desktop. Some supplied `test_*.py`, `probe_*.py` and diagnostic scripts deliberately open applications, type, speak or control windows; read each script before running it. Pytest-based checks additionally use `requirements-dev.txt`.

This source release has not been revalidated end to end on a fresh Windows installation. Microphone permissions, speech recognition accuracy, foreground focus, application layouts and optional GPU support vary by machine. CPU transcription can take time; a smaller Whisper model may improve responsiveness at the cost of recognition accuracy. CUDA auto-detection uses PyTorch when installed; the CPU path remains available without it.

Sparrow is designed for a supported set of desktop commands rather than unrestricted autonomous control.
