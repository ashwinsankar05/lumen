@echo off
rem LUMEN relay receiver: sessions measured on another laptop arrive here automatically.
rem Lab / data-collection mode (the other laptop does the measuring): see src\lumen\relay.py.
rem Give the address and token it prints to the other laptop (lumen-deepcheck.cmd, option 5).
setlocal
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
if not exist "%PY%" (
  echo Python environment not found at %PY%. Do the one-time setup in docs\RUNBOOK.md first.
  pause
  exit /b 1
)
set "TOKEN_FILE=data\remote\token.txt"
if not exist data\remote mkdir data\remote
if not exist "%TOKEN_FILE%" "%PY%" -c "import secrets; print(secrets.token_urlsafe(12))" > "%TOKEN_FILE%"
set /p TOKEN=<"%TOKEN_FILE%"
"%PY%" -m lumen.relay serve --token %TOKEN%
pause
