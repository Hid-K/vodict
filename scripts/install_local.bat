@echo off
rem Vodict local installer launcher (v2: clean TEMP + full setup log).
setlocal
set "TMP=C:\VodictTmp"
set "TEMP=C:\VodictTmp"
if not exist "%TMP%" mkdir "%TMP%"
taskkill /f /im Vodict.exe >nul 2>&1
start "" "%~dp0Vodict-setup-local.exe" /LOG="C:\VodictTmp\setup.log"
endlocal
