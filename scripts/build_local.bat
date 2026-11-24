@echo off
rem Сборка Vodict: exe + portable + установщик.
rem После сборки сбрасывается метка целостности установщика
rem (файлы из песочницы помечаются Low — без этого установщик
rem запускается с урезанным токеном и падает на записи в Temp).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_local.ps1" %*
if exist "%~dp0..\dist\installer\Vodict-setup-*.exe" icacls "%~dp0..\dist\installer\Vodict-setup-*.exe" /setintegritylevel M
echo.
echo Sborka zavershena. Ustanovshchik: dist\installer\Vodict-setup-*.exe (metka sbroshena)

