# Локальная сборка Vodict: тонкая обёртка над scripts/build_app.py
# (единая логика сборки с CI — одинаковые шаги и проверки).
#
#   scripts\build_local.bat              # exe + portable + установщик + smoke
#   scripts\build_local.bat -SkipInstaller
param(
    [switch]$SkipInstaller,
    [switch]$Run
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }

# conda PATH-фикс теперь внутри build_app.py — единая точка.

# Удаляем старые установщики: если файл занят (открыт в просмотрщике/
# удерживается интерфейсом) — ISCC упадёт с «in use» на перезаписи.
$oldInst = Join-Path $root 'dist\installer'
if (Test-Path $oldInst) {
    Get-ChildItem $oldInst -Filter 'Vodict-setup-*.exe' -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

# Устанавливаем зависимости движков в venv (не критично при неудаче
# отдельного пакета — соответствующий движок просто не попадёт в сборку)
Write-Host 'Проверяю/ставлю зависимости движков (vosk, sherpa-onnx)...'
foreach ($pkg in @('vosk>=0.3.45', 'sherpa-onnx>=1.10.30')) {
    cmd /c "`"$py`" -m pip install $pkg --quiet >nul 2>&1"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ПРЕДУПРЕЖДЕНИЕ: не удалось установить $pkg — движок будет недоступен в этой сборке"
    }
}

$flags = @('--zip', '--selftest')
if (-not $SkipInstaller) { $flags += '--installer' }
& $py (Join-Path $root 'scripts\build_app.py') @flags
if ($LASTEXITCODE -ne 0) { throw "build_app.py завершился с ошибкой (exit $LASTEXITCODE)" }

# Сброс Low-метки целостности у установщика: иначе он запускается с
# урезанным токеном и не может писать в %TEMP% («Ошибка 5»).
$setupExe = Join-Path $root 'dist\installer\Vodict-setup-0.3.1.exe'
if (Test-Path $setupExe) {
    icacls $setupExe /setintegritylevel M | Out-Null
}

if ($Run) {
    Write-Host 'Smoke уже выполнен внутри build_app.py (--selftest).'
}

Write-Host ''
Write-Host 'Готово:'
Write-Host '  приложение: dist\Vodict\Vodict.exe'
Get-ChildItem $root -Filter 'Vodict-windows-*.zip' |
    ForEach-Object { Write-Host "  portable:   $($_.FullName)" }
Get-ChildItem (Join-Path $root 'dist\installer') -Filter '*.exe' -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Host "  установщик: $($_.FullName)" }
