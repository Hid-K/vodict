"""Настройки приложения: dataclass + чтение/запись JSON в папке пользователя."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from platformdirs import user_config_dir

APP_NAME = "vodict"


def default_config_path() -> Path:
    """Путь к config.json по умолчанию (создаётся при первом сохранении).

    Windows:  %LOCALAPPDATA%\\vodict\\config.json
    macOS:    ~/Library/Application Support/vodict/config.json
    """
    return Path(user_config_dir(APP_NAME, appauthor=False)) / "config.json"


_VALID_MODES = {"push_to_talk", "toggle"}
_VALID_INSERTS = {"clipboard", "typing"}


def _warn(message: str) -> None:
    """Предупреждение в stderr, устойчивое к кодировке консоли (pipe cp1252)."""
    try:
        print(message, file=sys.stderr)
    except UnicodeEncodeError:
        try:
            safe = message.encode("ascii", "replace").decode("ascii")
            print(safe, file=sys.stderr)
        except Exception:
            pass


def _sanitize_enum(cfg: "Config") -> None:
    """Чинит значения, записанные старыми версиями диалога настроек:
    тогда в конфиг попадали подписи («PTT — удерживать…») вместо значений."""
    if cfg.hotkey_mode not in _VALID_MODES:
        low = str(cfg.hotkey_mode).lower()
        cfg.hotkey_mode = "toggle" if ("switch" in low or "перекл" in low) else "push_to_talk"
        _warn(f"[config] hotkey_mode исправлен на {cfg.hotkey_mode!r}")
    if cfg.insert_method not in _VALID_INSERTS:
        low = str(cfg.insert_method).lower()
        cfg.insert_method = "typing" if ("эмул" in low or "typ" in low) else "clipboard"
        _warn(f"[config] insert_method исправлен на {cfg.insert_method!r}")


@dataclass
class Config:
    # --- распознавание -----------------------------------------------------
    engine: str = "faster-whisper"   # имя движка из dictation.engines.registry
    model_size: str = "small"        # размер модели whisper (tiny/base/small/...)
    language: str = ""               # "" — автоопределение, иначе "ru", "en", ...
    device: str = "auto"             # auto | cpu | cuda (для локальных движков)
    compute_type: str = "auto"       # auto | int8 | float16 | float32
    beam_size: int = 5
    initial_prompt: str = ""         # подсказка модели (стиль, глоссарий)
    vad_filter: bool = True          # отсекать тишину/шум (faster-whisper)

    # --- горячая клавиша ----------------------------------------------------
    hotkey: str = "ctrl+alt"         # синтаксис: "ctrl+alt", "cmd+shift+f9", ...
    hotkey_mode: str = "push_to_talk"  # push_to_talk (удерживать) | toggle

    # --- вставка текста -----------------------------------------------------
    insert_method: str = "clipboard"   # clipboard (Ctrl+V) | typing (эмуляция)
    restore_clipboard: bool = True     # вернуть прежнее содержимое буфера
    clipboard_restore_delay: float = 0.6  # пауза перед восстановлением буфера, с
    focus_settle_delay: float = 0.35   # пауза после активации окна, с

    # --- аудио ---------------------------------------------------------------
    microphone: str = ""             # "" — микрофон по умолчанию; иначе часть имени
    sample_rate: int = 16000
    max_duration: float = 120.0      # жёсткий лимит одной записи, с (0 — нет)
    silence_timeout: float = 0.0     # авто-стоп после N с тишины (0 — выключено)
    silence_rms_threshold: float = 0.004

    # --- облачный движок openai-api ------------------------------------------
    api_key: str = ""                # либо переменная окружения OPENAI_API_KEY
    api_base_url: str = ""           # для совместимых endpoint'ов
    api_model: str = "whisper-1"

    # --- интерфейс -----------------------------------------------------------
    start_in_tray: bool = True       # запуск свёрнутым в трей (работа в фоне)
    sound_enabled: bool = True       # звуковые сигналы начала/конца записи
    always_on_top: bool = True

    # --- история диктовок -----------------------------------------------------
    history_enabled: bool = True     # сохранять тексты и метаданные
    save_audio: bool = True          # сохранять wav-записи голоса
    history_dir: str = ""            # пусто = ~/Vodict

    # -----------------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        path = Path(path) if path else default_config_path()
        data: dict = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {}
            except (OSError, json.JSONDecodeError) as exc:
                _warn(f"[config] Не удалось прочитать {path}: {exc}")
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            _warn(
                f"[config] Игнорирую неизвестные ключи: {', '.join(sorted(unknown))}"
            )
        cfg = cls(**{k: v for k, v in data.items() if k in known})
        _sanitize_enum(cfg)
        return cfg

    def save(self, path: str | Path | None = None) -> Path:
        path = Path(path) if path else default_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def engine_signature(self) -> tuple:
        """Параметры, влияющие на создание движка (для отслеживания изменений)."""
        return (
            self.engine, self.model_size, self.device, self.compute_type,
            self.language, self.beam_size, self.initial_prompt, self.vad_filter,
            self.api_key, self.api_base_url, self.api_model,
        )
