# Деплой Vodict в %LOCALAPPDATA%\Programs\Vodict БЕЗ Inno Setup.
# Запускать в PowerShell ОТ АДМИНИСТРАТОРА из папки scripts\:
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_local.ps1
#
# Скрипт: закрывает работающий Vodict, чинит права (если Defender
# портил ACL), удаляет старую установку, копирует свежий билд из
# dist\Vodict, пересоздаёт ярлыки (Пуск + Рабочий стол).
$ErrorActionPreference = 'Stop'

$target = Join-Path $env:LOCALAPPDATA 'Programs\Vodict'
$src = Join-Path (Split-Path -Parent $PSScriptRoot) 'dist\Vodict'

if (-not (Test-Path "$src\Vodict.exe")) {
    throw "Сначала соберите билд: scripts\build_local.bat (не найден $src\Vodict.exe)"
}
if (([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) -eq $false) {
    throw 'Запустите PowerShell от администратора (нужно чинить права на старой папке)'
}

Write-Host "[1/5] Закрываю работающий Vodict..."
taskkill /f /im Vodict.exe 2>$null | Out-Null

Write-Host "[2/5] Чиню права и удаляю старую установку: $target"
if (Test-Path $target) {
    takeown /f $target /r /d y | Out-Null
    icacls $target /reset /t /c | Out-Null
    Remove-Item $target -Recurse -Force
}

Write-Host "[3/5] Копирую свежий билд..."
New-Item $target -ItemType Directory -Force | Out-Null
Copy-Item (Join-Path $src '*') $target -Recurse -Force

Write-Host "[4/5] Создаю ярлыки..."
$wsh = New-Object -ComObject WScript.Shell
$lnk = $wsh.CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Vodict.lnk")
$lnk.TargetPath = Join-Path $target 'Vodict.exe'
$lnk.WorkingDirectory = $target
$lnk.Save()
$desk = [Environment]::GetFolderPath('Desktop')
$lnk2 = $wsh.CreateShortcut(Join-Path $desk 'Vodict.lnk')
$lnk2.TargetPath = Join-Path $target 'Vodict.exe'
$lnk2.WorkingDirectory = $target
$lnk2.Save()

Write-Host "[5/5] Автозапуск при входе (как в установщике)..."
$run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
Set-ItemProperty -Path $run -Name 'Vodict' -Value (Join-Path $target 'Vodict.exe')

Write-Host ''
Write-Host "Готово! Установлено: $target"
Write-Host 'Запустите Vodict из меню Пуск или с ярлыка на рабочем столе.'
