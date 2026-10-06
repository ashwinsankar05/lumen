@echo off
rem LUMEN demo menu. Double-click this file, or run it from a terminal in the project folder.
rem Every session asks whether the person in front of the camera is ready first.
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
if not exist "%PY%" (
  echo Python environment not found at %PY%. Do the one-time setup in docs\RUNBOOK.md first.
  pause
  exit /b 1
)

rem "lumen-demo.cmd 7" runs option 7 once and exits; with no argument the menu repeats.
set "ARG=%~1"
set "DONE="
set "EMPTY=0"

:menu
if defined DONE exit /b 0
if defined ARG (
  set "choice=%ARG%"
  set "DONE=1"
  goto dispatch
)
echo.
echo ================= LUMEN demo =================
echo  1  Check the camera            (8 s grey + 30 s probe)
echo  2  Genuine session             (8 s grey + 30 s probe)  expect VERIFY
echo  3  Replay attack               (replays the newest saved video)  expect REJECT
echo  4  Lamp test                   (torch on the face, from the front)  expect ABSTAIN
echo  5  Record a calibration session (60 s, saves video)
echo  6  Calibrate from session ids  (three genuine sessions in this room)
echo  7  Verify the newest record    (signature + one-time code)
echo  8  List sessions
echo  9  Open the operator console   (http://127.0.0.1:8000, Ctrl+C to stop)
echo  0  Quit
echo ==============================================
set "choice="
set /p choice=Choose:
if not defined choice (
  rem no input: stop after a few empty reads (closed input), instead of looping forever
  set /a EMPTY+=1
  if !EMPTY! geq 3 exit /b 0
  goto menu
)
set "EMPTY=0"

:dispatch
if "%choice%"=="1" goto smoke
if "%choice%"=="2" goto genuine
if "%choice%"=="3" goto replay
if "%choice%"=="4" goto lamp
if "%choice%"=="5" goto calsession
if "%choice%"=="6" goto calibrate
if "%choice%"=="7" goto verify
if "%choice%"=="8" goto list
if "%choice%"=="9" goto serve
if "%choice%"=="0" exit /b 0
goto menu

:ready
set "ok="
set /p ok=Is the person in front of the screen ready? Screen brightness 100%%, ~40 cm, facing it, still. (y/n):
if /i not "%ok%"=="y" ( echo Not started. & exit /b 1 )
exit /b 0

:smoke
call :ready || goto menu
"%PY%" -m lumen smoke
goto menu

:genuine
call :ready || goto menu
"%PY%" -m lumen run --duration 60 --label demo-genuine
goto menu

:lamp
echo Hold a phone torch 30-50 cm in front of the face, slightly above eye level, aimed at the face.
call :ready || goto menu
"%PY%" -m lumen run --duration 60 --label demo-lamp
goto menu

:calsession
set "lbl="
set /p lbl=Label for this calibration session (e.g. E1):
if "%lbl%"=="" set lbl=cal
call :ready || goto menu
"%PY%" -m lumen run --duration 60 --label %lbl% --save-video
goto menu

:replay
set "video="
for /f "delims=" %%d in ('dir /b /ad /o-d data\sessions 2^>nul') do (
  if not defined video if exist "data\sessions\%%d\video.mkv" set "video=data\sessions\%%d\video.mkv"
)
if not defined video ( echo No saved video yet: record one with option 5 first. & goto menu )
echo Replaying !video! as if it were the camera. Nobody needs to sit at the camera; the probe fills the screen.
set "ok="
set /p ok=Start the replay? (y/n):
if /i not "%ok%"=="y" goto menu
"%PY%" -m lumen run --source "file:!video!" --duration 30 --label demo-replay
goto menu

:calibrate
echo Session ids (folder names) of THREE genuine sessions recorded in this room:
dir /b /o-d data\sessions 2>nul
set "ids="
set /p ids=Paste the three ids separated by spaces:
if "%ids%"=="" goto menu
"%PY%" -m lumen calibrate %ids%
goto menu

:verify
set "rec="
for /f "delims=" %%d in ('dir /b /ad /o-d data\sessions 2^>nul') do (
  if not defined rec if exist "data\sessions\%%d\record.json" set "rec=data\sessions\%%d\record.json"
)
if not defined rec ( echo No records yet. & goto menu )
echo Verifying !rec!
"%PY%" -m lumen verify "!rec!"
goto menu

:list
dir /b /o-d data\sessions 2>nul
goto menu

:serve
echo Open http://127.0.0.1:8000 in a browser. Press Ctrl+C here to stop the console.
"%PY%" -m lumen serve
goto menu
