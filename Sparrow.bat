@echo off
rem Double-click this. It is the "on" button.
rem
rem Opens a small console window that stays open and listens. Keep it
rem minimised during a presentation - the keys Sparrow sends go to whatever
rem window you clicked last, so this one must not have focus.
rem
rem Close the window, or press Ctrl+C in it, to turn Sparrow off.

title Sparrow - hold Ctrl+Alt and speak
cd /d "%~dp0"

rem -u so the transcript lines appear immediately instead of being buffered.
python -u main.py

rem If python crashed, keep the window open long enough to read why.
if errorlevel 1 (
    echo.
    echo ---- Sparrow stopped with an error. Window stays open. ----
    pause
)
