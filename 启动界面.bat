@echo off
chcp 65001 >nul
title doclab
cd /d "%~dp0"

rem ---- locate a python that can import pypdf --------------------------
set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if exist "%PY%" goto :checkdep
echo   [!] bundled python not found, falling back to "python" on PATH
set "PY=python"

:checkdep
"%PY%" -c "import pypdf" 1>nul 2>nul
if not errorlevel 1 goto :run

echo   [!] pypdf missing, installing ...
"%PY%" -m pip install pypdf
if errorlevel 1 goto :depfail

:run
echo.
echo   doclab is starting ...
echo     URL   http://127.0.0.1:8765/
echo     Stop  close this window  (or press Ctrl-C)
echo.
"%PY%" doclab.py ui --port 8765
echo.
echo   doclab stopped.
pause
exit /b 0

:depfail
echo.
echo   [x] pip install failed. Check network / proxy, then retry:
echo       "%PY%" -m pip install pypdf
echo.
pause
exit /b 1
