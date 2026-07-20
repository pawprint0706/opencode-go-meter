@echo off
REM Create the virtualenv and install dependencies (Windows).
cd /d "%~dp0"

REM Stop a running instance first - Windows locks loaded .pyd files, so
REM pip would fail to upgrade packages while the app is running.
if exist .venv\Scripts\python.exe (
    echo Stopping any running instance...
    .venv\Scripts\python.exe launch.py --stop >nul 2>&1
)

py -3 -m venv .venv 2>nul || python -m venv .venv
if not exist .venv\Scripts\python.exe (
    echo Failed to create .venv - install Python 3.10+ from python.org first.
    pause
    exit /b 1
)

.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo Dependency installation failed.
    pause
    exit /b 1
)

echo.
echo Done. Starting OpenCode Go Meter...
if exist .venv\Scripts\pythonw.exe (
    start "" .venv\Scripts\pythonw.exe launch.py --replace
) else (
    start "" .venv\Scripts\python.exe launch.py --replace
)
echo Tip: enable "Start at Login" from the tray menu to launch it at boot.
pause
