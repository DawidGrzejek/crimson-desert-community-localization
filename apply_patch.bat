@echo off
rem Installs a translation patch on Windows: drag the .cdpatch.jsonl file onto this script
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    python -m venv .venv || goto :error
    .venv\Scripts\pip install -q lz4 cryptography || goto :error
)
.venv\Scripts\python apply_patch.py %*
pause
exit /b
:error
echo Python 3 is required: https://www.python.org/downloads/
pause
