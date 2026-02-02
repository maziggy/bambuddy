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
REM NOTE: Python version is intentionally pinned to a specific portable build.
REM       If you upgrade the bundled Python runtime, update PYTHON_VER here
REM       and make sure it matches the version used in download/installation logic.
if not defined PYTHON_VER set "PYTHON_VER=3.13.1"
REM Default Node.js version for the portable runtime. Override by setting NODE_VER before running this script.
if not defined NODE_VER set "NODE_VER=22.12.0"
REM NOTE: FFmpeg is not downloaded automatically.
REM       Install from the official site and add it to PATH:
REM       https://ffmpeg.org/download.html

REM Pinned SHA256 hashes for downloads (update when bumping versions)
set "GET_PIP_SHA256=dffc3658baada4ef383f31c3c672d4e5e306a6e376cee8bee5dbdf1385525104"
set "PYTHON_ZIP_HASH_AMD64=7b7923ff0183a8b8fca90f6047184b419b108cb437f75fc1c002f9d2f8bcec16"
set "PYTHON_ZIP_HASH_ARM64=ae8561bf958f77c68cb6c44ced983e5267fe965a7e4168f41ec2291350b81d55"
set "NODE_ZIP_HASH_X64=2b8f2256382f97ad51e29ff71f702961af466c4616393f767455501e6aece9b8"
set "NODE_ZIP_HASH_ARM64=17401720af48976e3f67c41e8968a135fb49ca1f88103a92e0e8c70605763854"

REM Detect system architecture (amd64 or arm64)
set "PYTHON_ARCH=amd64"
set "NODE_ARCH=x64"
if /I "%PROCESSOR_ARCHITECTURE%"=="ARM64" (
    set "PYTHON_ARCH=arm64"
    set "NODE_ARCH=arm64"
)
if defined PROCESSOR_ARCHITEW6432 (
    if /I "%PROCESSOR_ARCHITEW6432%"=="ARM64" (
        set "PYTHON_ARCH=arm64"
        set "NODE_ARCH=arm64"
    )
)

set "PYTHON_ZIP_HASH_EXPECTED=%PYTHON_ZIP_HASH_AMD64%"
if /I "%PYTHON_ARCH%"=="arm64" set "PYTHON_ZIP_HASH_EXPECTED=%PYTHON_ZIP_HASH_ARM64%"

set "NODE_ZIP_HASH_EXPECTED=%NODE_ZIP_HASH_X64%"
if /I "%NODE_ARCH%"=="arm64" set "NODE_ZIP_HASH_EXPECTED=%NODE_ZIP_HASH_ARM64%"

if not defined PORT set "PORT=8000"

REM Validate PORT is a number in the range 1-65535
echo(!PORT!| findstr /R "^[0-9][0-9]*$" >nul
if errorlevel 1 (
    echo Invalid PORT value "%PORT%". PORT must be an integer between 1 and 65535.
    exit /b 1
)

if %PORT% LSS 1 (
    echo Invalid PORT value "%PORT%". PORT must be between 1 and 65535.
    exit /b 1
)
if %PORT% GTR 65535 (
    echo Invalid PORT value "%PORT%". PORT must be between 1 and 65535.
    exit /b 1
)

REM ---- Handle arguments ----
if /i "%~1"=="reset" (
    echo Cleaning up portable environment...
    call :safe_rmdir "%PORTABLE%" ".portable"
    if errorlevel 1 exit /b 1
    call :safe_rmdir "%ROOT%\static" "static"
    if errorlevel 1 exit /b 1
    echo Done. Run again without arguments to set up fresh.
    pause
    exit /b 0
)

if /i "%~1"=="update" (
    echo Forcing dependency update and frontend rebuild...
    if exist "%PORTABLE%\.deps-installed" del "%PORTABLE%\.deps-installed"
    call :safe_rmdir "%ROOT%\static" "static"
    if errorlevel 1 exit /b 1
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

echo [1/6] Downloading Python %PYTHON_VER% (portable)...

if not exist "%PORTABLE%" mkdir "%PORTABLE%"
if not exist "%PYTHON_DIR%" mkdir "%PYTHON_DIR%"

curl -L --fail --show-error --progress-bar -o "%PORTABLE%\python.zip" ^
    "https://www.python.org/ftp/python/%PYTHON_VER%/python-%PYTHON_VER%-embed-%PYTHON_ARCH%.zip"
if errorlevel 1 (
    echo [ERROR] Failed to download Python.
    pause
    exit /b 1
)
call :verify_sha256 "%PORTABLE%\python.zip" "%PYTHON_ZIP_HASH_EXPECTED%" "Python"
if errorlevel 1 (
    del "%PORTABLE%\python.zip" >nul 2>&1
    pause
    exit /b 1
)

echo Extracting Python...
tar -xf "%PORTABLE%\python.zip" -C "%PYTHON_DIR%"
if errorlevel 1 (
    echo [ERROR] Failed to extract Python archive.
    del "%PORTABLE%\python.zip" >nul 2>&1
    pause
    exit /b 1
)
del "%PORTABLE%\python.zip"
if not exist "%PYTHON_DIR%\python.exe" (
    echo [ERROR] Python executable not found after extraction.
    pause
    exit /b 1
)

REM Enable site-packages by rewriting the ._pth file
REM Derive python tag (e.g., 3.13.x -> 313) from %PYTHON_VER%
for /f "tokens=1,2 delims=." %%A in ("%PYTHON_VER%") do (
    set "PY_MAJOR=%%A"
    set "PY_MINOR=%%B"
)
set "PYTHON_TAG=%PY_MAJOR%%PY_MINOR%"
(
    echo python!PYTHON_TAG!.zip
    echo .
    echo import site
) > "%PYTHON_DIR%\python!PYTHON_TAG!._pth"

REM ============================================
REM  Step 2: Install pip
REM ============================================
echo.
echo [2/6] Installing pip...

curl -L --fail -sS -o "%PORTABLE%\get-pip.py" "https://bootstrap.pypa.io/get-pip.py"
if errorlevel 1 (
    echo [ERROR] Failed to download get-pip.py.
    pause
    exit /b 1
)
call :verify_sha256 "%PORTABLE%\get-pip.py" "%GET_PIP_SHA256%" "get-pip.py"
if errorlevel 1 (
    del "%PORTABLE%\get-pip.py" >nul 2>&1
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
REM  Step 2.5: Create Virtual Environment (best effort)
REM ============================================
set "VENV_DIR=%PORTABLE%\venv"
set "PYTHON_EXE=%PYTHON_DIR%\python.exe"
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo.
    echo Creating virtual environment [optional]...
    "%PYTHON_DIR%\python.exe" -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [WARN] Failed to create virtual environment. Continuing without venv.
    )
)
if exist "%VENV_DIR%\Scripts\python.exe" (
    set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
)

REM ============================================
REM  Step 3: Install Python Dependencies
REM ============================================
if exist "%PORTABLE%\.deps-installed" (
    echo [OK] Python packages found.
    goto :deps_ready
)

echo.
echo [3/6] Installing Python packages (this may take a few minutes)...
if exist "%ROOT%\requirements.lock" (
    "%PYTHON_EXE%" -m pip install -r "%ROOT%\requirements.lock" --require-hashes --no-warn-script-location -q
) else (
    echo [WARN] requirements.lock not found. Falling back to requirements.txt - no hash enforcement.
    "%PYTHON_EXE%" -m pip install -r "%ROOT%\requirements.txt" --no-warn-script-location -q
)
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
REM  Step 4-6: Build Frontend (if needed)
REM ============================================
if exist "%ROOT%\static\index.html" (
    echo [OK] Frontend found.
    goto :frontend_ready
)

REM ---- Download Node.js if needed ----
if exist "%NODE_DIR%\node.exe" goto :node_ready

echo.
echo [4/6] Downloading Node.js %NODE_VER% (portable)...

curl -L --fail --show-error --progress-bar -o "%PORTABLE%\node.zip" ^
    "https://nodejs.org/dist/v%NODE_VER%/node-v%NODE_VER%-win-%NODE_ARCH%.zip"
if errorlevel 1 (
    echo [ERROR] Failed to download Node.js.
    pause
    exit /b 1
)
call :verify_sha256 "%PORTABLE%\node.zip" "%NODE_ZIP_HASH_EXPECTED%" "Node.js"
if errorlevel 1 (
    del "%PORTABLE%\node.zip" >nul 2>&1
    pause
    exit /b 1
)

echo Extracting Node.js...
tar -xf "%PORTABLE%\node.zip" -C "%PORTABLE%"
if errorlevel 1 (
    echo [ERROR] Failed to extract Node.js archive.
    del "%PORTABLE%\node.zip" >nul 2>&1
    pause
    exit /b 1
)
if exist "%PORTABLE%\node-v%NODE_VER%-win-%NODE_ARCH%" (
    ren "%PORTABLE%\node-v%NODE_VER%-win-%NODE_ARCH%" node
)
del "%PORTABLE%\node.zip"
echo [OK] Node.js %NODE_VER% ready.

:node_ready

REM ---- Build frontend ----
echo.
echo [5/6] Building frontend (this may take a while)...

set "PATH=%NODE_DIR%;%PATH%"

pushd "%ROOT%\frontend"

if exist "%ROOT%\frontend\package-lock.json" (
    call "%NODE_DIR%\npm.cmd" ci
) else (
    call "%NODE_DIR%\npm.cmd" install
)
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
if not exist "%ROOT%\frontend\static\index.html" (
    echo [ERROR] Frontend build did not produce static\index.html.
    echo        Expected: "%ROOT%\frontend\static\index.html"
    pause
    exit /b 1
)
if not exist "%ROOT%\static\index.html" (
    echo [ERROR] Frontend build did not produce static\index.html.
    echo        Expected: "%ROOT%\static\index.html"
    pause
    exit /b 1
)
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
echo [6/6] FFmpeg not found.
echo [INFO] Install FFmpeg from the official site and add it to PATH:
echo        https://ffmpeg.org/download.html
echo [INFO] Timelapse features will be unavailable until FFmpeg is installed.

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

REM Open browser after server is ready (poll localhost)
start /b cmd /c "for /l %%i in (1,1,30) do (curl -s -f -o nul http://localhost:%PORT% && (start http://localhost:%PORT% & exit /b 0) & timeout /t 1 /nobreak >nul)"

REM Launch the application
"%PYTHON_EXE%" -m uvicorn backend.app.main:app --host 0.0.0.0 --port %PORT% --loop asyncio

echo.
echo Bambuddy has stopped.
pause

endlocal
goto :eof

REM ============================================
REM  Helpers
REM ============================================
:safe_rmdir
set "TARGET=%~1"
set "LABEL=%~2"
if "%TARGET%"=="" (
    echo [ERROR] %LABEL% path is empty. Aborting.
    exit /b 1
)
if /I "%TARGET%"=="\" (
    echo [ERROR] %LABEL% path resolved to root. Aborting.
    exit /b 1
)
if not exist "%TARGET%" exit /b 0
echo Deleting "%TARGET%"
rmdir /s /q "%TARGET%"
if errorlevel 1 (
    echo [ERROR] Failed to delete "%TARGET%".
    exit /b 1
)
exit /b 0

:verify_sha256
set "FILE=%~1"
set "EXPECTED=%~2"
set "LABEL=%~3"
if "%EXPECTED%"=="" (
    echo [ERROR] %LABEL% checksum not found.
    exit /b 1
)
set "ACTUAL="
for /f "tokens=1" %%H in ('certutil -hashfile "%FILE%" SHA256 ^| findstr /R /I "^[0-9A-F][0-9A-F]"') do (
    set "ACTUAL=%%H"
    goto :hash_done
)
:hash_done
if not defined ACTUAL (
    echo [ERROR] Failed to compute SHA256 for %LABEL%.
    exit /b 1
)
if /I not "%ACTUAL%"=="%EXPECTED%" (
    echo [ERROR] SHA256 verification failed for %LABEL%.
    echo [INFO] Expected: %EXPECTED%
    echo [INFO] Actual:   %ACTUAL%
    exit /b 1
)
exit /b 0
