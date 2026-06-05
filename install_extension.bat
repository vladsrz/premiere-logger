@echo off
setlocal

echo === Premiere Logger — CEP Extension Install ===
echo.

set "SRC=%~dp0extension"
set "DEST=%APPDATA%\Adobe\CEP\extensions\premiere-logger"

echo [1/3] Copying extension to CEP folder and writing config...
if not exist "%DEST%" mkdir "%DEST%"
xcopy /E /I /Y "%SRC%" "%DEST%" >nul
if errorlevel 1 (
    echo ERROR: Failed to copy extension files.
    pause & exit /b 1
)

REM Write config.json so the extension knows where the Python scripts live
set "BASE=%~dp0"
set "BASE=%BASE:~0,-1%"
echo {"basePath": "%BASE:\=\\%"} > "%DEST%\config.json"
echo       Done: %DEST%

echo.
echo [2/3] Enabling unsigned CEP extensions (all CSXS versions)...
for %%V in (9 10 11 12 13) do (
    reg add "HKCU\Software\Adobe\CSXS.%%V" /v "PlayerDebugMode" /t REG_SZ /d "1" /f >nul 2>&1
)
echo       Done.

echo.
echo [3/3] Removing old startup entry (CEP extension handles launching now)...
schtasks /delete /tn "PremiereLogger" /f >nul 2>&1
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
if exist "%STARTUP%\PremiereLogger.vbs" del "%STARTUP%\PremiereLogger.vbs"
echo       Done.

echo.
echo === All done! ===
echo.
echo  1. Restart Premiere Pro
echo  2. Open:  Window ^> Extensions ^> Premiere Logger
echo  3. The panel will auto-start and auto-track from now on
echo.
echo  Full dashboard: http://localhost:5757
echo.
pause
