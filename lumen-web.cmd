@echo off
rem LUMEN verification link: this laptop is the verifier. Other laptops open the https:// address it
rem prints in Chrome; this laptop can use https://localhost:8443. Separate from the demo and call menus.
setlocal
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
if not exist "%PY%" (
  echo Python environment not found at %PY%. Do the one-time setup in docs\RUNBOOK.md first.
  pause
  exit /b 1
)
"%PY%" -m lumen.web %*
pause
