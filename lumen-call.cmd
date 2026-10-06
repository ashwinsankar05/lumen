@echo off
rem LUMEN over a video call (Google Meet). Double-click this file, or run it from the project folder.
rem The probe goes out as our camera through OBS Virtual Camera; LUMEN reads the other person's tile.
rem Separate from lumen-demo.cmd (the local demo), which this file does not change.
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
set REGION_FILE=data\call_region.txt
set THRESHOLDS=data\thresholds-call.json
if not exist "%PY%" (
  echo Python environment not found at %PY%. Do the one-time setup in docs\RUNBOOK.md first.
  pause
  exit /b 1
)
if not exist data mkdir data
set "EMPTY=0"

:menu
echo.
echo ================ LUMEN over Google Meet ================
echo  Before you start (your laptop):
echo   - OBS is CLOSED (LUMEN drives OBS Virtual Camera itself)
echo   - Meet camera = "OBS Virtual Camera"; blur, effects and "Adjust video lighting" OFF
echo   - the other person's tile pinned, Meet full screen (F11)
echo  The other person: your tile pinned, Meet full screen, brightness 100%%,
echo   ~40 cm from the screen, facing it, room light dim and steady, effects OFF
echo -------------------------------------------------------
if exist "%REGION_FILE%" (
  set /p REGION=<"%REGION_FILE%"
  echo  Selected tile: !REGION!
) else (
  echo  Selected tile: none yet - use option 1 first
)
echo.
echo  1  Select the other person's video tile   (15 s to minimise this window)
echo  2  Call session                           (8 s grey + 90 s probe in the call)
echo  3  Verify the newest record               (signature + one-time code)
echo  0  Quit
echo =======================================================
set "choice="
set /p choice=Choose:
rem three empty answers in a row (e.g. input closed) end the menu instead of looping
if not defined choice (
  set /a EMPTY+=1
  if !EMPTY! geq 3 exit /b 0
  goto menu
)
set "EMPTY=0"
if "%choice%"=="1" goto pick
if "%choice%"=="2" goto session
if "%choice%"=="3" goto verify
if "%choice%"=="0" exit /b 0
goto menu

:pick
echo Minimise this window NOW and leave Meet full screen. A screenshot is taken in 15 s;
echo then drag a box round the other person's video tile (face, neck, some background), press Enter.
timeout /t 15 /nobreak
set "REGION="
for /f "usebackq delims=" %%L in (`%PY% -m lumen pick-region ^| findstr /b "screen:"`) do set "REGION=%%L"
if not defined REGION ( echo No tile selected. & goto menu )
> "%REGION_FILE%" echo !REGION!
echo Saved: !REGION!   Do not move or resize the Meet window from now on.
goto menu

:session
if not exist "%REGION_FILE%" ( echo Select the tile first ^(option 1^). & goto menu )
if not exist "%THRESHOLDS%" (
  echo %THRESHOLDS% not found: calibrate through a call first ^(three genuine call sessions, see docs\RUNBOOK.md^).
  goto menu
)
set /p REGION=<"%REGION_FILE%"
set "ok="
set /p ok=Ask the other person: ready? Seated ~40 cm, facing the screen, still for 100 s. Did they say yes? (y/n):
if /i not "%ok%"=="y" ( echo Not started. & goto menu )
set "lbl="
set /p lbl=Label for this session (e.g. meet-genuine-1, Enter for meet-demo):
if not defined lbl set "lbl=meet-demo"
echo Minimise this window and bring Meet back to full screen: the session starts in 15 s.
timeout /t 15 /nobreak
"%PY%" -m lumen run --emit virtualcam --source "!REGION!" --amplitude 34 --duration 90 --thresholds "%THRESHOLDS%" --label "!lbl!"
goto menu

:verify
call "%~dp0lumen-demo.cmd" 7
goto menu
