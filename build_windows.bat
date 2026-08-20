@echo off
REM =====================================================================
REM  VideoDownloader - Windows build script
REM  Produces dist\VideoDownloader.exe using PyInstaller
REM =====================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo  === Multi-Platform Video Downloader build ===
echo.

REM --- 1. Locate Python -----------------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo         Install Python 3.12+ from https://www.python.org/downloads/
    echo         and make sure "Add python.exe to PATH" is checked.
    pause
    exit /b 1
)

REM --- 2. Create virtual environment -----------------------------------------
if not exist ".venv" (
    echo [1/5] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 goto :fail
)

call ".venv\Scripts\activate.bat"

REM --- 3. Install dependencies -----------------------------------------------
echo [2/5] Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 goto :fail

REM --- 4. Run tests -----------------------------------------------------------
echo [3/5] Running test suite...
set QT_QPA_PLATFORM=offscreen
python -m pytest tests -q
if errorlevel 1 (
    echo.
    echo [WARN] Tests failed. Build will continue, but verify before shipping.
)

REM --- 5. Copy FFmpeg if available locally (optional) ------------------------
REM Production FFmpeg license (LGPL/GPL) must be respected; the app also
REM auto-detects an installed FFmpeg at runtime.
if not exist "ffmpeg" (
    echo [INFO] No local "ffmpeg" folder. The app will auto-detect FFmpeg on
    echo        the system PATH. Place ffmpeg.exe here to bundle it.
)

REM --- 6. Build the executable ------------------------------------------------
echo [4/5] Building VideoDownloader.exe (PyInstaller)...
python -m PyInstaller build.spec --noconfirm --clean
if errorlevel 1 goto :fail

REM --- 7. Copy supporting files ------------------------------------------------
echo [5/5] Finalizing build...
if exist "ffmpeg\ffmpeg.exe" (
    copy /y "ffmpeg\ffmpeg.exe" "dist\ffmpeg.exe" >nul
)
if exist ".env.example" (
    copy /y ".env.example" "dist\.env.example" >nul
)
echo.
echo  === Build complete ===
echo.
echo  Executable : dist\VideoDownloader.exe
echo  Note       : FFmpeg is required for stream merging and audio
echo               extraction. It is auto-detected from PATH; or set its
echo               location in Settings ^> Advanced ^> FFmpeg Path.
echo.
pause
exit /b 0

:fail
echo.
echo [ERROR] Build failed. See messages above.
pause
exit /b 1
