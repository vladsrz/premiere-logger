@echo off
setlocal

echo === Premiere Logger - Setup ===
echo.

echo [1/2] Installing Python dependencies...
pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo.
    echo ERROR: pip install failed. Make sure Python is installed and in PATH.
    pause
    exit /b 1
)

echo.
echo [2/2] Adding to Windows Startup folder (no admin required)...

set "SCRIPT=%~dp0start.pyw"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "VBS=%STARTUP%\PremiereLogger.vbs"

(
    echo Set WshShell = CreateObject^("WScript.Shell"^)
    echo WshShell.Run "pythonw ""%SCRIPT%""", 0, False
) > "%VBS%"

if exist "%VBS%" (
    echo Autostart configured -- logger will run at every login.
    echo Location: %VBS%
) else (
    echo WARNING: Could not write to Startup folder.
    echo You can still start the logger manually by double-clicking start.pyw.
)

echo.
echo === Done! ===
echo.
echo  Start now:    double-click start.pyw
echo  Dashboard:    http://localhost:5757
echo  Uninstall:    del "%VBS%"
echo.
pause
