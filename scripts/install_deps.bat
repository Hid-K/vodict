@echo off
rem Установка всех зависимостей Vodict в venv (с видимым прогрессом).
rem Прокси (опционально): scripts\install_deps.bat -Proxy socks5://127.0.0.1:1080
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_deps.ps1" %*
pause
