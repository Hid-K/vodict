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
    parser.add_argument("--engine", help="движок: faster-whisper | tone | vosk | sherpa | openai-whisper | openai-api")
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
    parser.add_argument("--history", nargs="?", const=20, type=int, metavar="N",
                        help="показать последние N записей истории диктовок и выйти")
    parser.add_argument("--diagnostics", action="store_true",
                        help="собрать диагностику в файл и выйти")
    parser.add_argument("--debug", action="store_true",
                        help="подробные логи (stderr + файл лога)")
    parser.add_argument("--selftest", action="store_true",
                        help="проверить загрузку UI-стека (tkinter) и выйти")
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


def show_history(config, limit: int) -> int:
    from .history import HistoryStore

    rows = HistoryStore(config.history_dir or None).read_index(max(1, limit))
    if not rows:
        print("История пуста — сделайте первую диктовку.")
        return 0
    print(f"{'Время':<20} {'Сек':>6} {'Скор':>6}  {'Модель':<24} {'Устр':<5} "
          f"{'Кач':<7} {'Beam':>4} {'Яз':<3} {'Статус':<8} Текст")
    for r in rows:
        seconds = r.get("seconds", 0.0)
        speed = r.get("speed")
        speed_text = f"×{speed}" if speed else "—"
        beam = r.get("beam_size")
        dev = r.get("device_used") or "—"
        print(f"{r.get('time', ''):<20} {seconds:>5.1f} {speed_text:>6}  "
              f"{r.get('model', ''):<24} {dev:<5} "
              f"{r.get('compute_type', ''):<7} "
              f"{str(beam) if beam else '—':>4} {r.get('language', ''):<3} "
              f"{r.get('status', ''):<8} {r.get('preview', '')}")
    print(f"\nПоказано: {len(rows)}. Аудиозаписи: ~/Vodict/recordings/")
    return 0


def main(argv=None) -> int:
    _ensure_printable_console()
    args = build_parser().parse_args(argv)

    from .config import default_config_path
    from .logging_setup import setup_logging

    logfile = setup_logging(args.debug)
    if args.debug:
        print(f"Лог (DEBUG): {logfile}")

    if args.list_mics:
        return list_microphones()

    if args.selftest:
        # Импортирует bootstrap -> tkinter: детектирует сборку без tcl/tk.
        from . import bootstrap  # noqa: F401
        from .ui.window import DictationWindow  # noqa: F401

        print("selftest OK")
        return 0

    from .config import Config

    config = Config.load(args.config)
    apply_overrides(config, args)

    from .proxy import apply_proxy_env
    apply_proxy_env(config)

    if args.history is not None:
        return show_history(config, args.history)

    if args.diagnostics:
        from .diagnostics import collect

        try:
            path = collect(config)
        except Exception as exc:
            print(f"Не удалось собрать диагностику: {exc}")
            return 1
        print(f"Диагностика собрана: {path}")
        print("Отправьте этот файл тому, кто помогает с отладкой.")
        return 0

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
        from .bootstrap import run_app
        run_app(config, controller)
    finally:
        controller.shutdown()
    return 0
