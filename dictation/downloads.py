"""Менеджер загрузки моделей faster-whisper с прогрессом.

Модель качается заранее (при выборе в настройках) через
``huggingface_hub.snapshot_download`` с собственным tqdm-хуком, который
агрегирует прогресс по всем файлам. После загрузки ``WhisperModel``
находит модель в кэше и создаётся мгновенно, без повторной загрузки.

События публикуются в колбэк ``on_event(kind, payload)``:
  started  {"model"}                  — загрузка началась
  progress {"model", "percent"}       — процент (0..100), может быть None
  done     {"model"}                  — загрузка завершена
  error    {"model", "error"}         — ошибка
"""
from __future__ import annotations

import concurrent.futures
import queue
import shutil
import threading
import time
from pathlib import Path
from typing import Callable

REPO_TEMPLATE = "Systran/faster-whisper-{model}"
ALLOW_PATTERNS = [
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
]

# Модели, которые известны менеджеру (совпадают со списком faster-whisper)
KNOWN_MODELS = [
    "tiny.en", "tiny", "base.en", "base", "small.en", "small",
    "medium.en", "medium", "large-v1", "large-v2", "large-v3", "large",
    "large-v3-turbo", "turbo",
    "distil-small.en", "distil-medium.en", "distil-large-v2", "distil-large-v3",
]


class _Tracker:
    """Агрегирует прогресс по всем скачиваемым файлам (по объектам tqdm)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._files: dict[int, tuple[float, float]] = {}

    def reset(self) -> None:
        with self._lock:
            self._files.clear()

    def on_update(self, bar) -> None:
        with self._lock:
            self._files[id(bar)] = (float(bar.n or 0), float(bar.total or 0))

    def progress(self) -> float | None:
        with self._lock:
            done = sum(n for n, _ in self._files.values())
            total = sum(t for _, t in self._files.values())
        if total <= 0:
            return None
        return max(0.0, min(100.0, done / total * 100.0))


def _make_hook(tracker: _Tracker):
    from tqdm import tqdm

    class _Hook(tqdm):
        def update(self, n: int = 1) -> None:
            super().update(n)
            tracker.on_update(self)

        def close(self) -> None:
            tracker.on_update(self)
            super().close()

    return _Hook


class DownloadManager:
    """Очередь загрузок моделей + отслеживание состояния каждой модели.

    Состояния: "downloading" (progress 0..100 или None), "available",
    "error". Модель, отсутствующая в состояниях, ещё не проверялась —
    ``is_cached`` проверит локальный кэш HF.
    """

    def __init__(self, on_event: Callable[[str, dict], None] | None = None) -> None:
        self.on_event = on_event
        self._lock = threading.Lock()
        self._states: dict[str, dict] = {}
        self._cached: set[str] = set()
        self._sizes: dict[str, int] = {}
        self._queue: queue.Queue[str] = queue.Queue()
        self._tracker = _Tracker()
        self._hook_cls = _make_hook(self._tracker)
        self._worker = threading.Thread(
            target=self._worker, name="model-downloads", daemon=True
        )
        self._worker.start()

    # ------------------------------------------------------------------
    def state(self, model: str) -> dict | None:
        with self._lock:
            st = self._states.get(model)
            return dict(st) if st else None

    def states(self) -> dict[str, dict]:
        with self._lock:
            return {m: dict(s) for m, s in self._states.items()}

    def is_cached(self, model: str) -> bool:
        """Проверяет локальный кэш HF (без сети)."""
        with self._lock:
            if model in self._cached:
                return True
            st = self._states.get(model)
            if st and st["state"] == "downloading":
                return False
        return self._snapshot_path(model) is not None

    def cached_models(self) -> list[str]:
        """Список скачанных моделей (из известных)."""
        return [m for m in KNOWN_MODELS if self.is_cached(m)]

    def model_size_bytes(self, model: str) -> int | None:
        """Размер модели на диске (кэшируется)."""
        with self._lock:
            if model in self._sizes:
                return self._sizes[model]
        root = self._snapshot_path(model)
        if root is None:
            return None
        model_root = self._model_root(root)
        if model_root is None:
            return None
        total = 0
        for f in model_root.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                continue
        with self._lock:
            self._sizes[model] = total
        return total

    def delete_model(self, model: str) -> int:
        """Удаляет модель из кэша HF. Возвращает освобождённые байты."""
        root = self._snapshot_path(model)
        if root is None:
            raise FileNotFoundError(f"Модель «{model}» не найдена в кэше")
        model_root = self._model_root(root)
        if model_root is None:
            raise FileNotFoundError(f"Не удалось определить папку модели «{model}»")
        total = 0
        for f in model_root.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                continue
        shutil.rmtree(model_root)
        with self._lock:
            self._cached.discard(model)
            self._sizes.pop(model, None)
            self._states.pop(model, None)
        self._emit("deleted", {"model": model})
        return total

    @staticmethod
    def _model_root(snapshot_path: Path) -> Path | None:
        """.../snapshots/<hash> -> .../models--Org--name"""
        if len(snapshot_path.parents) >= 2:
            return snapshot_path.parents[1]
        return None

    def _snapshot_path(self, model: str) -> Path | None:
        try:
            from huggingface_hub import snapshot_download

            path = snapshot_download(
                repo_id=self._repo(model),
                local_files_only=True,
                allow_patterns=ALLOW_PATTERNS,
            )
            return Path(path)
        except Exception:
            return None

    def request_download(self, model: str) -> str:
        """Запустить загрузку. Возвращает "cached" | "already" | "started"."""
        if self.is_cached(model):
            return "cached"
        with self._lock:
            st = self._states.get(model)
            if st and st["state"] == "downloading":
                return "already"
            self._states[model] = {"state": "downloading", "progress": None,
                                   "error": None}
        self._emit("started", {"model": model})
        self._queue.put(model)
        return "started"

    def wait_until_cached(self, model: str, timeout: float = 7200.0) -> None:
        """Блокируется, пока модель качается (для создания движка)."""
        t0 = time.time()
        while True:
            if self.is_cached(model):
                return
            st = self.state(model)
            if st and st["state"] == "error":
                raise RuntimeError(
                    f"Ошибка загрузки модели {model}: {st.get('error')}"
                )
            if time.time() - t0 > timeout:
                return
            time.sleep(0.5)

    # ------------------------------------------------------------------
    @staticmethod
    def _repo(model: str) -> str:
        if "/" in model:  # пользовательский repo_id с HuggingFace
            return model
        return REPO_TEMPLATE.format(model=model)

    def _download(self, model: str) -> None:
        from huggingface_hub import snapshot_download

        self._tracker.reset()
        snapshot_download(
            repo_id=self._repo(model),
            allow_patterns=ALLOW_PATTERNS,
            tqdm_class=self._hook_cls,
        )

    def _worker(self) -> None:
        while True:
            model = self._queue.get()
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    fut = pool.submit(self._download, model)
                    last_percent: float | None = None
                    while not fut.done():
                        p = self._tracker.progress()
                        if p is not None and p != last_percent:
                            last_percent = p
                            with self._lock:
                                st = self._states.get(model)
                                if st:
                                    st["progress"] = p
                            self._emit("progress", {"model": model, "percent": p})
                        time.sleep(0.25)
                    fut.result()  # пробрасывает исключение загрузки
                with self._lock:
                    self._cached.add(model)
                    st = self._states.get(model)
                    if st:
                        st["state"] = "available"
                        st["progress"] = 100.0
                self._emit("progress", {"model": model, "percent": 100.0})
                self._emit("done", {"model": model})
            except Exception as exc:
                with self._lock:
                    st = self._states.get(model)
                    if st:
                        st["state"] = "error"
                        st["progress"] = None
                        st["error"] = str(exc)
                self._emit("error", {"model": model, "error": str(exc)})

    def _emit(self, kind: str, payload: dict) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(kind, payload)
        except Exception:
            pass  # события не должны ронять загрузчик
