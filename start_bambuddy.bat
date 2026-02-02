@echo off

chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

title Bambuddy

REM ============================================
REM  Bambuddy Portable Launcher for Windows
REM
REM  Double-click to start. First run downloads
REM  Python and Node.js automatically (portable,
REM  no system changes). Everything is stored in
REM  the .portable\ folder.
REM
REM  Usage:
REM    start_bambuddy.bat            Launch
REM    start_bambuddy.bat update     Update deps & rebuild frontend
REM    start_bambuddy.bat reset      Clean all & fresh start
REM    set PORT=9000 & start_bambuddy.bat   Change port
REM ============================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "PORTABLE=%ROOT%\.portable"
set "PYTHON_DIR=%PORTABLE%\python"
set "NODE_DIR=%PORTABLE%\node"
set "FFMPEG_DIR=%PORTABLE%\ffmpeg"
set "PYTHON_VER=3.13.1"
set "NODE_VER=22.12.0"

if not defined PORT set "PORT=8000"

REM ---- Handle arguments ----
if /i "%~1"=="reset" (
    echo Cleaning up portable environment...
    if exist "%PORTABLE%" rmdir /s /q "%PORTABLE%"
    if exist "%ROOT%\static" rmdir /s /q "%ROOT%\static"
    echo Done. Run again without arguments to set up fresh.
    pause
    exit /b 0
)

if /i "%~1"=="update" (
    echo Forcing dependency update and frontend rebuild...
    if exist "%PORTABLE%\.deps-installed" del "%PORTABLE%\.deps-installed"
    if exist "%ROOT%\static" rmdir /s /q "%ROOT%\static"
)

REM ---- Check prerequisites ----
where curl >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] curl.exe is not available.
    echo         Windows 10 version 1803 or later is required.
    echo.
    pause
    exit /b 1
)
where tar >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] tar.exe is not available.
    echo         Windows 10 version 1803 or later is required.
    echo.
    pause
    exit /b 1
)

REM ---- Verify project structure ----
if not exist "%ROOT%\backend\app\main.py" (
    echo.
    echo [ERROR] backend\app\main.py not found.
    echo         This script must be in the Bambuddy project root.
    echo.
    pause
    exit /b 1
)

echo.
echo  ____                  _               _     _
echo ^| __ )  __ _ _ __ ___ ^| ^|__  _   _  __^| ^| __^| ^|_   _
echo ^|  _ \ / _` ^| '_ ` _ \^| '_ \^| ^| ^| ^|/ _` ^|/ _` ^| ^| ^| ^|
echo ^| ^|_) ^| (_^| ^| ^| ^| ^| ^| ^| ^|_) ^| ^|_^| ^| (_^| ^| (_^| ^| ^|_^| ^|
echo ^|____/ \__,_^|_^| ^|_^| ^|_^|_.__/ \__,_^|\__,_^|\__,_^|\__, ^|
echo                                                ^|___/
echo.

REM ============================================
REM  Step 1: Setup Portable Python
REM ============================================
if exist "%PYTHON_DIR%\python.exe" (
    echo [OK] Python %PYTHON_VER% found.
    goto :python_ready
)

echo [1/5] Downloading Python %PYTHON_VER% (portable)...

if not exist "%PORTABLE%" mkdir "%PORTABLE%"
if not exist "%PYTHON_DIR%" mkdir "%PYTHON_DIR%"

curl -L --progress-bar -o "%PORTABLE%\python.zip" ^
    "https://www.python.org/ftp/python/%PYTHON_VER%/python-%PYTHON_VER%-embed-amd64.zip"
if errorlevel 1 (
    echo [ERROR] Failed to download Python.
    pause
    exit /b 1
)

echo Extracting Python...
tar -xf "%PORTABLE%\python.zip" -C "%PYTHON_DIR%"
del "%PORTABLE%\python.zip"

REM Enable site-packages by rewriting the ._pth file
(
    echo python313.zip
    echo .
    echo import site
) > "%PYTHON_DIR%\python313._pth"

REM ============================================
REM  Step 2: Install pip
REM ============================================
echo.
echo [2/5] Installing pip...

curl -L -s -o "%PORTABLE%\get-pip.py" "https://bootstrap.pypa.io/get-pip.py"
if errorlevel 1 (
    echo [ERROR] Failed to download get-pip.py.
    pause
    exit /b 1
)

"%PYTHON_DIR%\python.exe" "%PORTABLE%\get-pip.py" --no-warn-script-location -q
if errorlevel 1 (
    echo [ERROR] Failed to install pip.
    pause
    exit /b 1
)
del "%PORTABLE%\get-pip.py"

echo [OK] Python %PYTHON_VER% ready.

:python_ready

REM ============================================
REM  Step 3: Install Python Dependencies
REM ============================================
if exist "%PORTABLE%\.deps-installed" (
    echo [OK] Python packages found.
    goto :deps_ready
)

echo.
echo [3/5] Installing Python packages (this may take a few minutes)...

REM Ensure setuptools and wheel are available (required for building from source)
"%PYTHON_DIR%\python.exe" -m pip install setuptools wheel --no-warn-script-location -q

"%PYTHON_DIR%\python.exe" -m pip install -r "%ROOT%\requirements.txt" --no-warn-script-location -q
if errorlevel 1 (
    echo [ERROR] Failed to install Python packages.
    pause
    exit /b 1
)

REM Create marker file
echo %date% %time% > "%PORTABLE%\.deps-installed"
echo [OK] Packages installed.

:deps_ready

REM ============================================
REM  Step 4-5: Build Frontend (if needed)
REM ============================================
if exist "%ROOT%\static\index.html" (
    echo [OK] Frontend found.
    goto :frontend_ready
)

REM ---- Download Node.js if needed ----
if exist "%NODE_DIR%\node.exe" goto :node_ready

echo.
echo [4/5] Downloading Node.js %NODE_VER% (portable)...

curl -L --progress-bar -o "%PORTABLE%\node.zip" ^
    "https://nodejs.org/dist/v%NODE_VER%/node-v%NODE_VER%-win-x64.zip"
if errorlevel 1 (
    echo [ERROR] Failed to download Node.js.
    pause
    exit /b 1
)

echo Extracting Node.js...
tar -xf "%PORTABLE%\node.zip" -C "%PORTABLE%"
if exist "%PORTABLE%\node-v%NODE_VER%-win-x64" (
    ren "%PORTABLE%\node-v%NODE_VER%-win-x64" node
)
del "%PORTABLE%\node.zip"
echo [OK] Node.js %NODE_VER% ready.

:node_ready

REM ---- Build frontend ----
echo.
echo [5/5] Building frontend (this may take a while)...

set "PATH=%NODE_DIR%;%PATH%"

pushd "%ROOT%\frontend"

call "%NODE_DIR%\npm.cmd" install
if errorlevel 1 (
    echo [ERROR] npm install failed.
    popd
    pause
    exit /b 1
)

call "%NODE_DIR%\npm.cmd" run build
if errorlevel 1 (
    echo [ERROR] Frontend build failed.
    popd
    pause
    exit /b 1
)

popd
echo [OK] Frontend built.

:frontend_ready

REM ============================================
REM  Step 6: Setup Portable FFmpeg (if needed)
REM ============================================
where ffmpeg >nul 2>&1
if not errorlevel 1 (
    echo [OK] FFmpeg found in system PATH.
    goto :ffmpeg_ready
)

if exist "%FFMPEG_DIR%\bin\ffmpeg.exe" (
    echo [OK] FFmpeg found.
    goto :ffmpeg_ready
)

echo.
echo [6/6] Downloading FFmpeg (portable)...

if not exist "%FFMPEG_DIR%" mkdir "%FFMPEG_DIR%"

curl -L --progress-bar -o "%PORTABLE%\ffmpeg.zip" ^
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
if errorlevel 1 (
    echo [WARN] Failed to download FFmpeg. Timelapse features will be unavailable.
    goto :ffmpeg_ready
)

echo Extracting FFmpeg...
tar -xf "%PORTABLE%\ffmpeg.zip" -C "%PORTABLE%"

REM Move bin from versioned directory to expected location
for /d %%D in ("%PORTABLE%\ffmpeg-*") do (
    if exist "%%D\bin\ffmpeg.exe" (
        xcopy "%%D\bin" "%FFMPEG_DIR%\bin\" /E /Y >nul
        rmdir /s /q "%%D"
        goto :ffmpeg_extracted
    )
)
:ffmpeg_extracted
del "%PORTABLE%\ffmpeg.zip" 2>nul
echo [OK] FFmpeg ready.

:ffmpeg_ready

REM ============================================
REM  Launch Bambuddy
REM ============================================
echo.
echo ================================================
echo   Bambuddy is starting on port %PORT%
echo   Open: http://localhost:%PORT%
echo.
echo   Press Ctrl+C to stop
echo ================================================
echo.

REM Set PYTHONPATH so "backend.app.main" module is found
set "PYTHONPATH=%ROOT%"

REM Add portable FFmpeg to PATH if available
if exist "%FFMPEG_DIR%\bin\ffmpeg.exe" set "PATH=%FFMPEG_DIR%\bin;%PATH%"

REM Open browser after a short delay
start /b cmd /c "timeout /t 4 /nobreak >nul 2>&1 & start http://localhost:%PORT%"

REM Launch the application
"%PYTHON_DIR%\python.exe" -m uvicorn backend.app.main:app --host 0.0.0.0 --port %PORT% --loop asyncio

echo.
echo Bambuddy has stopped.
pause

endlocal
