@echo off
rem Локальная сборка Vodict (portable + установщик при наличии Inno Setup).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_local.ps1" %*
