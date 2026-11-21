"""Прокси для сетевых операций с HuggingFace (и OpenAI API).

huggingface_hub (через свой HTTP-клиент) и openai-клиент читают стандартные
переменные окружения HTTPS_PROXY/HTTP_PROXY/ALL_PROXY при создании клиента.
``apply_proxy_env`` прописывает их из конфигурации до первого сетевого
обращения. Поддерживаются схемы http, https, socks5, socks5h (для socks
нужен пакет socksio — он в requirements).
"""
from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

log = logging.getLogger(__name__)

PROXY_ENV_VARS = (
    "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
    "https_proxy", "http_proxy", "all_proxy",
)


def normalize_proxy_url(url: str) -> str:
    """«host:port» -> «socks5://host:port»; пробельные — убирает."""
    url = (url or "").strip()
    if url and "://" not in url:
        url = "socks5://" + url
    return url


def mask_proxy_url(url: str) -> str:
    """Скрывает логин:пароль в URL прокси (для логов и диагностики)."""
    url = (url or "").strip()
    if "://" not in url:
        return url
    scheme, _, rest = url.partition("://")
    creds, _, host = rest.rpartition("@")
    if creds:
        return f"{scheme}://***@{host}"
    return url


def apply_proxy_env(config) -> None:
    """Прописывает/убирает прокси в переменных окружения процесса.

    Вызывать до первого сетевого обращения к HuggingFace/OpenAI (клиенты
    читают окружение при создании). Если прокси выключен — переменные
    убираются (включая унаследованные из системы).
    """
    if getattr(config, "proxy_enabled", False):
        url = normalize_proxy_url(getattr(config, "proxy_url", ""))
        if url:
            for var in PROXY_ENV_VARS:
                os.environ[var] = url
            log.info("Прокси применён: %s", mask_proxy_url(url))
            return
    for var in PROXY_ENV_VARS:
        os.environ.pop(var, None)
