@echo off
rem Vodict local installer launcher.
rem - closes a running Vodict instance (setup must replace its exe)
rem - uses a clean TEMP dir (some launch contexts deny writes to %TEMP%)
setlocal
set "TMP=%~dp0tmp_install"
set "TEMP=%~dp0tmp_install"
if not exist "%TMP%" mkdir "%TMP%"
taskkill /f /im Vodict.exe >nul 2>&1
start "" "%~dp0Vodict-setup-local.exe"
endlocal
