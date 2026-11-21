# Локальная сборка Vodict: portable + (опционально) установщик.
# Без GitHub: python .venv\Scripts\python.exe и packaging/vodict.spec.
#
# Использование (из корня репозитория):
#   powershell -ExecutionPolicy Bypass -File scripts\build_local.ps1            # portable + установщик (если есть ISCC)
#   powershell -ExecutionPolicy Bypass -File scripts\build_local.ps1 -SkipInstaller
#   powershell -ExecutionPolicy Bypass -File scripts\build_local.ps1 -Run       # + smoke-прогон собранного exe
param(
    [switch]$SkipInstaller,
    [switch]$Run
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }

# Если Python из conda — добавляем Library\bin и DLLs в PATH ДО сборки:
# иначе PyInstaller не сможет допросить _tkinter (tcl86t.dll/tk86t.dll
# лежат в Library\bin) и бандл соберётся без tcl/tk.
$basePrefix = (& $py -c "import sys; print(sys.base_prefix)").Trim()
if (Test-Path (Join-Path $basePrefix 'Library\bin\ffi-8.dll')) {
    Write-Host "[0/4] conda-окружение: добавляю $basePrefix\Library\bin и DLLs в PATH"
    $env:PATH = "$basePrefix\Library\bin;$basePrefix\DLLs;$basePrefix;$env:PATH"
}

Write-Host "[1/4] PyInstaller: $py -m PyInstaller packaging/vodict.spec"
& $py -m PyInstaller --noconfirm packaging/vodict.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller завершился с ошибкой' }

Write-Host "[2/4] Portable-архив"
$zip = Join-Path $root 'Vodict-local-portable.zip'
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path 'dist/Vodict/*' -DestinationPath $zip
Get-Item $zip | Select-Object Name, @{n = 'MB'; e = { [math]::Round($_.Length / 1MB, 1) } } | Format-Table

$iscc = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if ($SkipInstaller) {
    Write-Host '[3/4] Установщик пропущен (-SkipInstaller)'
}
elseif ($iscc) {
    Write-Host "[3/4] Установщик (Inno Setup): $iscc"
    & $iscc /DMyAppVersion=local packaging/installer.iss
    if ($LASTEXITCODE -ne 0) { throw 'Сборка установщика (ISCC) завершилась с ошибкой' }
    Get-Item 'dist/installer/Vodict-setup-local.exe' |
        Select-Object FullName, @{n = 'MB'; e = { [math]::Round($_.Length / 1MB, 1) } } | Format-Table
}
else {
    Write-Host '[3/4] Inno Setup не найден — установлен только portable.'
    Write-Host '      Для локальных установщиков: winget install JRSoftware.InnoSetup'
}

if ($Run) {
    Write-Host '[4/4] Smoke: Vodict.exe --selftest (UI-стек + микрофоны в frozen-сборке)'
    # --selftest импортирует bootstrap (tkinter) и не пишет в конфиг/журнал,
    # поэтому безопасно даже при запущенном установленном Vodict.
    cmd /c "dist\Vodict\Vodict.exe --selftest >nul 2>&1"
    if ($LASTEXITCODE -ne 0) { throw 'Smoke-прогон exe завершился с ошибкой' }
    Write-Host '      exe отвечает: OK (UI-стек загружается)'
}
else {
    Write-Host '[4/4] Smoke пропущен (-Run для прогона exe)'
}

Write-Host ''
Write-Host 'Готово:'
Write-Host "  portable:   $zip"
if (Test-Path 'dist/installer/Vodict-setup-local.exe') {
    Write-Host '  установщик: dist\installer\Vodict-setup-local.exe'
}
Write-Host '  приложение: dist\Vodict\Vodict.exe'
