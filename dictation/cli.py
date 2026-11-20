"""Точка входа командной строки: разбор аргументов и запуск приложения."""
from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vodict",
        description="Vodict — голосовая диктовка: нажал клавишу — сказал — текст появился "
                    "в активном поле ввода.",
    )
    parser.add_argument("--config", help="путь к config.json (по умолчанию — в "
                                         "пользовательской папке конфигов)")
    parser.add_argument("--engine", help="движок: faster-whisper | openai-whisper | openai-api")
    parser.add_argument("--model", dest="model_size", help="размер модели whisper")
    parser.add_argument("--language", help='язык речи: "ru", "en"... (по умолчанию — авто)')
    parser.add_argument("--hotkey", help='комбинация клавиш, напр. "ctrl+alt"')
    parser.add_argument("--mode", choices=["push_to_talk", "toggle"],
                        help="режим горячей клавиши")
    parser.add_argument("--insert", choices=["clipboard", "typing"],
                        dest="insert_method", help="способ вставки текста")
    parser.add_argument("--mic", dest="microphone",
                        help="имя (или часть имени) микрофона")
    parser.add_argument("--list-mics", action="store_true",
                        help="показать микрофоны и выйти")
    return parser


def list_microphones() -> int:
    try:
        from .audio.recorder import list_input_devices
    except ImportError as exc:
        print(f"Не хватает зависимости: {exc}\nУстановите: pip install -r requirements.txt")
        return 1
    devices = list_input_devices()
    if not devices:
        print("Микрофоны не найдены.")
        return 0
    for dev in devices:
        default = "  (по умолчанию)" if dev["is_default"] else ""
        print(f"[{dev['index']}] {dev['name']} — "
              f"{int(dev['default_samplerate'])} Гц{default}")
    return 0


def apply_overrides(config, args) -> None:
    if args.engine:
        config.engine = args.engine
    if args.model_size:
        config.model_size = args.model_size
    if args.language:
        config.language = args.language
    if args.hotkey:
        config.hotkey = args.hotkey
    if args.mode:
        config.hotkey_mode = args.mode
    if args.insert_method:
        config.insert_method = args.insert_method
    if args.microphone:
        config.microphone = args.microphone


def _ensure_printable_console() -> None:
    """Не падать с UnicodeEncodeError при выводе кириллицы в pipe/редирект
    с однобайтовой кодировкой (на реальной консоли Windows Python и так
    печатает Unicode)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None) -> int:
    _ensure_printable_console()
    args = build_parser().parse_args(argv)
    if args.list_mics:
        return list_microphones()

    from .config import Config, default_config_path

    config = Config.load(args.config)
    apply_overrides(config, args)

    try:
        from .app import DictationController
    except ImportError as exc:
        print(f"Не хватает зависимости: {exc}\nУстановите: pip install -r requirements.txt")
        return 1

    if sys.platform == "darwin":
        print(
            "macOS: выдайте права в Системные настройки → Конфиденциальность и "
            "безопасность: «Микрофон», «Универсальный доступ» и «Мониторинг "
            "ввода» — для терминала/Python."
        )
    print(f"Конфигурация: {default_config_path()}")
    print(f"Движок: {config.engine} • модель: {config.model_size} • "
          f"клавиша: {config.hotkey} ({config.hotkey_mode})")

    controller = DictationController(config)
    try:
        controller.run()
    finally:
        controller.shutdown()
    return 0
