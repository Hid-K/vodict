"""Единая логика сборки Vodict — используется и локально, и в CI.

Гарантирует одинаковость артефактов: один и тот же spec, одинаковые
шаги (PyInstaller -> smoke --selftest -> portable -> установщик) и
одинаковые проверки. Запуск:

    python scripts/build_app.py --zip --installer --selftest [--version X]

Локально (по умолчанию) артефакты именуются с суффиксом -local; в CI
передавайте --version v0.3.0 — имена станут Vodict-windows-v0.3.0.zip и
Vodict-setup-v0.3.0.exe.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# Кириллица в выводе не должна ронять скрипт в cp1252-консолях
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
IS_WIN = platform.system() == "Windows"


def _run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    completed = subprocess.run(cmd, cwd=str(ROOT))
    if completed.returncode != 0:
        raise SystemExit(f"команда завершилась с ошибкой: {' '.join(cmd)}")


def _find_iscc() -> Path | None:
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Inno Setup 6"
        / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Inno Setup 6"
        / "ISCC.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    ]
    for c in candidates:
        if c.exists():
            return c
    which = shutil.which("ISCC")
    return Path(which) if which else None


def _selftest() -> None:
    """Smoke: exe импортирует весь UI-стек и видит микрофоны."""
    exe = DIST / "Vodict" / "Vodict.exe"
    if not exe.exists():
        raise SystemExit(f"не найден {exe}")
    print("smoke: Vodict.exe --selftest", flush=True)
    completed = subprocess.run([str(exe), "--selftest"], timeout=180)
    if completed.returncode != 0:
        raise SystemExit("smoke --selftest провален — см. logs/crash.log")


def _portable_zip(version: str) -> Path:
    src = DIST / "Vodict"
    zip_path = ROOT / f"Vodict-windows-{version}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(src))
    print(f"portable: {zip_path.name} "
          f"({zip_path.stat().st_size / 1e6:.1f} МБ)", flush=True)
    return zip_path


def _installer(version: str) -> Path | None:
    if not IS_WIN:
        print("установщик: пропущен (не Windows)")
        return None
    iscc = _find_iscc()
    if iscc is None:
        print("Inno Setup не найден — установщик не собран "
              "(winget install JRSoftware.InnoSetup)")
        return None
    out_dir = DIST / "installer"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Если старый установщик занят другим процессом (просмотрщик, Блокнот,
    # антивирус) — ISCC отчитается невнятным «in use». Проверяем заранее
    # и даём понятное сообщение.
    target = out_dir / f"Vodict-setup-{version}.exe"
    if target.exists():
        try:
            with open(target, "r+b"):
                pass
        except PermissionError as exc:
            raise SystemExit(
                f"Не могу перезаписать {target}: файл занят другим процессом. "
                f"Закройте программу, которая его держит (просмотрщик, "
                f"Блокнот, антивирус), удалите файл вручную и запустите "
                f"сборку снова. Подробно: {exc}"
            )

    _run([str(iscc), f"/DMyAppVersion={version}", *extra_args,
          str(ROOT / "packaging" / "installer.iss")])
    installer = target
    if not installer.exists():
        raise SystemExit(f"ISCC не создал {installer}")
    print(f"установщик: {installer.name} "
          f"({installer.stat().st_size / 1e6:.1f} МБ)", flush=True)
    return installer


def _fix_integrity(path: Path, recursive: bool = False) -> None:
    """Сброс Low-метки целостности на артефактах (Windows).

    Файлы, созданные из песочниц с низким IL, наследуют Low-метку (NW):
    запущенный из них процесс не может писать в Temp — «Ошибка 5».
    Повышать метки из изолированного контекста запрещено — тогда
    предупреждаем с готовой ручной командой (одной строкой, без
    многосекундного перебора тысяч файлов).
    """
    if not IS_WIN:
        return
    cmd = ["icacls", str(path), "/setintegritylevel", "M"]
    if recursive:
        cmd.append("/t")
    completed = subprocess.run(cmd, cwd=str(ROOT))
    if completed.returncode != 0:
        print(f"ПРЕДУПРЕЖДЕНИЕ: не удалось выставить целостность {path} "
              f"(exit {completed.returncode}). Выполните вручную от "
              f"пользователя: {' '.join(cmd)}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Сборка Vodict (CI и локально)")
    ap.add_argument("--version", default=None,
                    help="версия для имён артефактов (по умолчанию — из dictation)")
    ap.add_argument("--zip", action="store_true", help="собрать portable-архив")
    ap.add_argument("--installer", action="store_true",
                    help="собрать установщик (Windows + Inno Setup)")
    ap.add_argument("--selftest", action="store_true",
                    help="smoke-проверка собранного exe")
    args = ap.parse_args()

    if args.version is None:
        sys.path.insert(0, str(ROOT))
        from dictation import __version__ as args_version  # noqa: PLC0415
        args.version = args_version

    print(f"Версия: {args.version} | платформа: {platform.system()}", flush=True)

    # Очистка остатков сорванных установок pip (создаются в корне проекта,
    # если pip не смог писать в системный %TEMP%)
    for junk in ROOT.glob("pip-*"):
        shutil.rmtree(junk, ignore_errors=True)

    # Предупреждение о недостающих пакетах движков (не попадут в бандл)
    import importlib.util  # noqa: PLC0415

    missing = [m for m in ("vosk", "sherpa_onnx", "tone", "pyctcdecode")
               if importlib.util.find_spec(m) is None]
    if missing:
        print(f"ПРЕДУПРЕЖДЕНИЕ: в окружении сборки нет пакетов движков: "
              f"{', '.join(missing)} — эти движки не попадут в бандл. "
              f"Установите: pip install -r requirements-engines.txt",
              flush=True)

    # conda-окружение: Library\bin должен быть в PATH ДО запуска PyInstaller,
    # иначе хук tkinter не найдёт tcl/tk DLL и бандл соберётся без GUI
    # («DLL load failed while importing _tkinter»).
    base = Path(getattr(sys, "base_prefix", sys.prefix))
    conda_bin = base / "Library" / "bin"
    if (conda_bin / "ffi-8.dll").exists():
        os.environ["PATH"] = (
            f"{conda_bin}{os.pathsep}{base / 'DLLs'}{os.pathsep}"
            f"{base}{os.pathsep}{os.environ.get('PATH', '')}"
        )
        print(f"conda: добавлен PATH +{conda_bin}", flush=True)

    _run([sys.executable, "-m", "PyInstaller", "--noconfirm",
          str(ROOT / "packaging" / "vodict.spec")])

    if IS_WIN:
        _fix_integrity(DIST, recursive=True)

    if args.selftest:
        _selftest()
    if args.zip and IS_WIN:
        zip_path = _portable_zip(args.version)
        _fix_integrity(zip_path)
    if args.installer:
        installer = _installer(args.version)
        if installer is not None:
            _fix_integrity(installer)

    print("Готово.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
