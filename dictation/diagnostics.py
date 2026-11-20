"""Сбор диагностической информации в один текстовый файл.

Включает: версии, платформу, конфигурацию (с скрытым api_key), список
аудиоустройств, состояние кэша моделей, статистику истории и хвост лога.
Вызывается кнопкой «🩺 Диагностика» или ``python main.py --diagnostics``.
"""
from __future__ import annotations

import dataclasses
import json
import platform
import sys
import time
from pathlib import Path

from . import __version__
from .config import Config, default_config_path


def _audio_devices() -> list[str]:
    try:
        import sounddevice as sd

        rows = []
        for idx, dev in enumerate(sd.query_devices()):
            host = sd.query_hostapis(dev["hostapi"])["name"]
            rows.append(f"[{idx}] {dev['name']} "
                        f"(вх: {dev['max_input_channels']}, вых: {dev['max_output_channels']}, "
                        f"{host}, {int(dev['default_samplerate'])} Гц)")
        rows.append(f"устройство по умолчанию (вх/вых): {sd.default.device}")
        return rows
    except Exception as exc:
        return [f"ошибка: {exc}"]


def _models_info() -> list[str]:
    try:
        from .downloads import KNOWN_MODELS, DownloadManager

        mgr = DownloadManager()
        rows = []
        for m in KNOWN_MODELS:
            if mgr.is_cached(m):
                size = mgr.model_size_bytes(m)
                rows.append(f"{m}: в кэше"
                            + (f" ({size / 1e6:.0f} МБ)" if size else ""))
            else:
                rows.append(f"{m}: не скачана")
        return rows
    except Exception as exc:
        return [f"ошибка: {exc}"]


def _history_info(config: Config) -> list[str]:
    try:
        import json as _json

        from .history import HistoryStore

        store = HistoryStore(config.history_dir or None)
        rows = [f"папка: {store.root}"]
        index = store.root / "index.jsonl"
        if not index.exists():
            rows.append("индекс ещё не создан")
            return rows
        count = 0
        total_audio = 0.0
        total_transcribe = 0.0
        speed_counted = 0
        for line in index.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            count += 1
            seconds = float(r.get("seconds", 0) or 0)
            speed = float(r.get("speed", 0) or 0)
            if seconds > 0:
                total_audio += seconds
            if seconds > 0 and speed > 0:
                total_transcribe += seconds / speed
                speed_counted += 1
        rows.append(f"диктовок в индексе: {count}")
        rows.append(f"суммарная длительность аудио: {total_audio:.1f} с")
        if speed_counted:
            rows.append(f"суммарное время распознавания: {total_transcribe:.1f} с")
            rows.append(f"средняя скорость: ×{total_audio / total_transcribe:.1f} "
                        f"(по {speed_counted} диктовкам)")
        else:
            rows.append("скорость пока не измерялась")
        return rows
    except Exception as exc:
        return [f"ошибка: {exc}"]


def _log_tail(logfile: Path, lines: int = 200) -> list[str]:
    if not logfile.exists():
        return ["лог ещё не создан"]
    data = logfile.read_text(encoding="utf-8", errors="replace").splitlines()
    return data[-lines:]


def _config_json(config: Config) -> str:
    data = dataclasses.asdict(config)
    if data.get("api_key"):
        data["api_key"] = "***скрыто в целях безопасности***"
    return json.dumps(data, ensure_ascii=False, indent=2)


def collect(config: Config) -> Path:
    """Собирает диагностику и возвращает путь к созданному файлу."""
    cfg_dir = default_config_path().parent
    out_dir = cfg_dir / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / time.strftime("diagnostics_%Y%m%d_%H%M%S.txt")

    logfile = cfg_dir / "logs" / "app.log"

    lines: list[str] = []
    add = lines.append
    add("=== Vodict: диагностика ===")
    add(f"время: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    add(f"версия приложения: {__version__}")
    add(f"python: {sys.version}")
    add(f"платформа: {platform.platform()}")
    add(f"машина: {platform.machine()}")
    add("")

    add("--- конфигурация (api_key скрыт) ---")
    add(_config_json(config))
    add("")

    add("--- аудиоустройства ---")
    lines += _audio_devices()
    add("")

    add("--- модели faster-whisper (кэш HuggingFace) ---")
    lines += _models_info()
    add("")

    add("--- история диктовок ---")
    lines += _history_info(config)
    add("")

    add(f"--- лог: последние 200 строк ({logfile}) ---")
    lines += _log_tail(logfile)

    if sys.platform == "darwin":
        add("")
        add("--- macOS: чек-лист прав ---")
        add("Системные настройки → Конфиденциальность и безопасность:")
        add(" • Микрофон — доступ выдан этому приложению/терминалу")
        add(" • Универсальный доступ (Accessibility) — для хоткея и вставки")
        add(" • Мониторинг ввода (Input Monitoring) — для глобального хоткея")
        add("Если приложение скачано из интернета: xattr -cr <путь к .app>")

    # utf-8-sig: чтобы файл корректно открывался в Блокноте Windows
    out.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return out
