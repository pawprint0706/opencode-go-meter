@echo off
REM Uninstall OpenCode Go Meter (Windows): stop the app, remove the
REM autostart entry, and delete the app data folder and the virtualenv.
cd /d "%~dp0"

echo This will stop OpenCode Go Meter and remove:
echo   - the "Start at Login" registry entry
echo   - app data in "%USERPROFILE%\.opencode-go-meter" (including the saved login)
echo   - the .venv folder in this project
set /p CONFIRM="Continue? [y/N] "
if /i not "%CONFIRM%"=="y" (
    echo Cancelled.
    pause
    exit /b 0
)

echo.
echo Stopping any running instance and removing autostart...
REM Preferred path: let the app stop itself and unregister autostart.
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe launch.py --stop >nul 2>&1
    .venv\Scripts\python.exe -c "from go_meter import autostart; autostart.disable()" >nul 2>&1
)

REM Fallback: remove the Run key value directly, in case the .venv is gone.
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v OpenCodeGoMeter /f >nul 2>&1

echo Removing app data and .venv...
if exist "%USERPROFILE%\.opencode-go-meter" rmdir /s /q "%USERPROFILE%\.opencode-go-meter"
if exist .venv rmdir /s /q .venv

echo.
echo Done. OpenCode Go Meter has been removed.
echo You can now delete this project folder if you want: %~dp0
pause
