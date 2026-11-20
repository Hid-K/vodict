# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: сборка Vodict.

Windows: onedir + консоль (логи видны) -> архив в релиз.
macOS:   .app-бандл в COLLECT (консоли нет — логи в файле), упаковывается
         в .dmg. Сборка отложена (см. .github/workflows/release.yml).

Локальная сборка:  pyinstaller --noconfirm packaging/vodict.spec
"""
import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files

# Пути в spec отсчитываются от папки spec-файла, а проект — на уровень выше
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# Пакеты, требующие полного сбора (данные + бинарные DLL + сабмодули):
# - faster_whisper (assets silero-vad), ctranslate2 (ctypes-DLL),
#   sounddevice + _sounddevice_data (portaudio DLL)
# - pynput/pystray (динамический выбор бэкендов), windows_toasts + winrt
#   (нативные WinRT-модули), huggingface_hub, tqdm
PACKAGES = [
    "faster_whisper",
    "ctranslate2",
    "sounddevice",
    "_sounddevice_data",
    "pynput",
    "pystray",
    "pyperclip",
    "platformdirs",
    "huggingface_hub",
    "tqdm",
    "windows_toasts",
    "winrt",
]

datas = []
binaries = []
hiddenimports = []
for pkg in PACKAGES:
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    except Exception as exc:  # не хватает пакета — движок просто недоступен
        print(f"[spec] collect_all({pkg!r}) пропущен: {exc}")
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# onnxruntime: исключаем из анализа (импорт крашит изолированный подпроцесс
# PyInstaller) и кладём весь пакет как data-файлы: в onedir-сборке модуль
# импортируется с диска (_internal), DLL попадают вместе с пакетом.
datas += collect_data_files("onnxruntime", include_py_files=True)

hiddenimports += [
    "tkinter",
    # WinRT-проекции для windows_toasts (на всякий случай, не все видны анализу)
    "winrt._winrt",
    "winrt.system",
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
    "winrt.windows.data.xml.dom",
    "winrt.windows.ui.notifications",
]

is_mac = sys.platform == "darwin"

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=[
        # Импорт onnxruntime в изолированном подпроцессе PyInstaller
        # ненадёжен; полный пакет доставляется на диск (collect_data_files
        # выше) и в рантайме импортируется оттуда.
        "onnxruntime",
        "torch",
        "openai_whisper",
        "matplotlib",
        "PyQt5",
        "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Vodict",
    debug=False,
    strip=False,
    upx=False,
    # На macOS консоли нет (.app); на Windows консоль оставляем —
    # там видны логи при тестировании.
    console=not is_mac,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Vodict",
)

if is_mac:
    app = BUNDLE(
        coll,
        name="Vodict.app",
        icon=None,
        bundle_identifier="io.github.voicedictation.app",
    )
