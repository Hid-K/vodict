# Установка всех зависимостей Vodict в venv проекта (видимый прогресс).
# Запуск в ОБЫЧНОМ терминале из корня проекта:
#   scripts\install_deps.bat
# Если PyPI недоступен напрямую — можно включить прокси:
#   scripts\install_deps.bat -Proxy socks5://127.0.0.1:10808
param(
    [string]$Proxy = ""
)
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }

$proxyArgs = @()
if ($Proxy -ne "") {
    # socks-прокси требует PySocks; http-прокси работает сразу
    & $py -m pip install --quiet pysocks
    $proxyArgs = @('--proxy', $Proxy)
    Write-Host "Прокси: $Proxy"
}

Write-Host ''
Write-Host '=== 1/3: основной набор (faster-whisper, ctranslate2, av, onnxruntime...) ===' -ForegroundColor Cyan
Write-Host '   (~400 МБ загрузки — это долго, но НЕ зависание)' -ForegroundColor Yellow
& $py -m pip install --progress-bar on @proxyArgs -r (Join-Path $root 'requirements.txt') pyinstaller
if ($LASTEXITCODE -ne 0) { Write-Host 'ОШИБКА этапа 1 — пришлите вывод выше' -ForegroundColor Red; exit 1 }

Write-Host ''
Write-Host '=== 2/3: движки Vosk + Sherpa-onnx + pyctcdecode ===' -ForegroundColor Cyan
foreach ($pkg in @('vosk>=0.3.45', 'sherpa-onnx>=1.10.30', 'pyctcdecode>=0.2.0')) {
    Write-Host "  ставлю: $pkg"
    & $py -m pip install --progress-bar on @proxyArgs $pkg
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ПРЕДУПРЕЖДЕНИЕ: $pkg не установился — этот движок будет недоступен" -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host '=== 3/3: движок T-one (из GitHub) ===' -ForegroundColor Cyan
& $py -m pip install --progress-bar on @proxyArgs 'git+https://github.com/voicekit-team/T-one'
if ($LASTEXITCODE -ne 0) {
    Write-Host '  ПРЕДУПРЕЖДЕНИЕ: T-one не установился (проверьте доступ к GitHub или включите прокси)' -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'Проверка импортов всех движков:'
& $py -c "import faster_whisper, vosk, sherpa_onnx, tone, pyctcdecode; print('ВСЕ ДВИЖКИ ОК')" 2>&1 | Select-String -Pattern 'ВСЕ|No module' | ForEach-Object { Write-Host "  $_" }
Write-Host ''
Write-Host 'Готово. Теперь можно запускать scripts\build_local.bat'
