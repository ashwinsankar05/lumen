@echo off
rem LUMEN deep check: the 3D relief + lean live-swap check, on top of the normal L1-L3 verdict.
rem Separate from lumen-demo.cmd and lumen-call.cmd, which this file does not change.
rem The probe fills THIS laptop's screen; the subject sits in front of it (not through a video call:
rem the deep check is not validated through calls).
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
set DEEP=data\deep_thresholds.json
if not exist "%PY%" (
  echo Python environment not found at %PY%. Do the one-time setup in docs\RUNBOOK.md first.
  pause
  exit /b 1
)
set "EMPTY=0"

echo.
echo Which camera? 0 = this laptop's webcam. For a face-swap test, the OBS Virtual Camera index
echo (python -m lumen cameras lists them).
set "CAM="
set /p CAM=Camera index (Enter = 0):
if not defined CAM set "CAM=0"
set "LOCK="
set "virt="
set /p virt=Is it a virtual camera, e.g. OBS? (y/N):
if /i "%virt%"=="y" set "LOCK=--no-lock"

:menu
echo.
echo ================= LUMEN deep check (3D relief + lean) =================
echo  Camera: camera:%CAM% %LOCK%
if exist "%DEEP%" ( echo  Deep check calibrated: %DEEP% ) else ( echo  Deep check NOT calibrated yet: options 1 x3, then 2 )
echo -----------------------------------------------------------------------
echo  1  Genuine calibration session   (8 s grey + 90 s probe, saves video)
echo  2  Calibrate from the 3 newest genuine calibration sessions
echo  3  Live check                    (8 s grey + 90 s probe: L1-L3 verdict + deep check)
echo  4  Deep-check the newest session again
echo  0  Quit
echo =======================================================================
set "choice="
set /p choice=Choose:
if not defined choice (
  set /a EMPTY+=1
  if !EMPTY! geq 3 exit /b 0
  goto menu
)
set "EMPTY=0"
if "%choice%"=="1" goto genuine
if "%choice%"=="2" goto calibrate
if "%choice%"=="3" goto live
if "%choice%"=="4" goto again
if "%choice%"=="0" exit /b 0
goto menu

:ready
set "ok="
set /p ok=Is the person in front of the screen ready? Brightness 100%%, ~40 cm, facing it, still for 100 s. (y/n):
if /i not "%ok%"=="y" ( echo Not started. & exit /b 1 )
exit /b 0

:genuine
echo A REAL face (for a swap test: the same path with the swap switched OFF).
call :ready || goto menu
"%PY%" -m lumen.deepcheck live --source camera:%CAM% %LOCK% --label deep-genuine --save-video --deep-thresholds "%DEEP%"
goto menu

:calibrate
"%PY%" -m lumen.deepcheck calibrate --latest 3 --label deep-genuine --out "%DEEP%"
goto menu

:live
call :ready || goto menu
set "lbl="
set /p lbl=Label (e.g. deep-swap-on, deep-swap-off; Enter = deep-check):
if not defined lbl set "lbl=deep-check"
"%PY%" -m lumen.deepcheck live --source camera:%CAM% %LOCK% --label "!lbl!" --save-video --deep-thresholds "%DEEP%"
goto menu

:again
"%PY%" -m lumen.deepcheck check --latest 1 --thresholds "%DEEP%"
goto menu
