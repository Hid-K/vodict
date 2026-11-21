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
    _run([str(iscc), f"/DMyAppVersion={version}", str(ROOT / "packaging" / "installer.iss")])
    installer = out_dir / f"Vodict-setup-{version}.exe"
    if not installer.exists():
        raise SystemExit(f"ISCC не создал {installer}")
    print(f"установщик: {installer.name} "
          f"({installer.stat().st_size / 1e6:.1f} МБ)", flush=True)
    return installer


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
    _run([sys.executable, "-m", "PyInstaller", "--noconfirm",
          str(ROOT / "packaging" / "vodict.spec")])

    if args.selftest:
        _selftest()
    if args.zip and IS_WIN:
        _portable_zip(args.version)
    if args.installer:
        _installer(args.version)

    print("Готово.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
