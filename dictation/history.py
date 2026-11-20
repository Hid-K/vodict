"""История диктовок: записи голоса, метаданные и построчный индекс.

Расположение по умолчанию — в домашней директории пользователя:

    ~/Vodict/
      index.jsonl          — построчный индекс: одна JSON-строка на диктовку
      recordings/<id>.wav  — аудиозапись (16 бит PCM, mono)
      entries/<id>.json    — полные метаданные и распознанный текст

<id> = ГГГГММДД_ЧЧММСС_xxxx — локальное время начала записи + случайный
суффикс. Индекс дописывается (append-only), поэтому файл не перезаписывается
целиком и устойчив к сбоям.
"""
from __future__ import annotations

import json
import secrets
import time
import wave
from pathlib import Path

import numpy as np


def default_history_dir() -> Path:
    return Path.home() / "Vodict"


def new_entry_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(2)


class HistoryStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser() if root else default_history_dir()
        self._recordings = self.root / "recordings"
        self._entries = self.root / "entries"
        self._index = self.root / "index.jsonl"

    # ------------------------------------------------------------------
    def begin_entry(self, audio: np.ndarray, sample_rate: int,
                    save_audio: bool = True) -> dict:
        """Создаёт id, при необходимости сохраняет wav.

        Возвращает первые метаданные записи; остальные поля добавляет
        controller._finish_history перед записью в хранилище.
        """
        entry_id = new_entry_id()
        audio_file = None
        if save_audio and audio.size > 0:
            self._ensure_dirs()
            rel = f"recordings/{entry_id}.wav"
            pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
            with wave.open(str(self.root / rel), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(pcm.tobytes())
            audio_file = rel
        seconds = float(audio.shape[0]) / float(sample_rate) if sample_rate else 0.0
        return {
            "id": entry_id,
            "audio_file": audio_file,
            "audio_seconds": round(seconds, 2),
            "sample_rate": sample_rate,
        }

    def write_entry(self, meta: dict) -> None:
        """Сохраняет полные метаданные и дописывает строку в индекс."""
        self._ensure_dirs()
        entry_id = str(meta.get("id", new_entry_id()))
        meta = dict(meta, id=entry_id)
        (self._entries / f"{entry_id}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        row = {
            "id": entry_id,
            "time": meta.get("time", ""),
            "seconds": round(float(meta.get("audio_seconds", 0.0)), 2),
            "model": f"{meta.get('engine', '')}:{meta.get('model', '')}",
            "language": (meta.get("language_detected")
                         or meta.get("language_requested") or ""),
            "chars": len(meta.get("text", "") or ""),
            "status": meta.get("status", ""),
            "preview": (meta.get("text", "") or "")[:80],
            "has_audio": bool(meta.get("audio_file")),
        }
        with self._index.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def read_index(self, limit: int = 50) -> list[dict]:
        """Последние ``limit`` записей индекса, свежие сверху."""
        if not self._index.exists():
            return []
        lines = self._index.read_text(encoding="utf-8").splitlines()
        out = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # повреждённая строка не должна ломать всю историю
        out.reverse()
        return out

    # ------------------------------------------------------------------
    def _ensure_dirs(self) -> None:
        self._recordings.mkdir(parents=True, exist_ok=True)
        self._entries.mkdir(parents=True, exist_ok=True)
