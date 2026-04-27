"""Обёртка над gigachat SDK с retry.

Публичный интерфейс (AIClient.complete) сохранён от прошлой Anthropic-реализации,
поэтому модули ai_generator / ai_validator / pipeline остаются нетронутыми.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from gigachat import GigaChat
from gigachat.exceptions import AuthenticationError, ResponseError
from gigachat.models import Chat, Messages, MessagesRole
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

load_dotenv()


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

DEFAULT_MODEL = "GigaChat-2-Max"
DEFAULT_SCOPE = "GIGACHAT_API_PERS"


def load_prompt(name: str) -> str:
    """Прочитать промпт из prompts/<name>.md."""
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


class AIClient:
    """Тонкая обёртка вокруг GigaChat.chat.

    Использование:
        client = AIClient()
        text = client.complete(system="...", user="...")
    """

    def __init__(self, model: Optional[str] = None, max_tokens: int = 2048) -> None:
        credentials = os.getenv("GIGACHAT_CREDENTIALS")
        if not credentials:
            raise RuntimeError(
                "Не задан GIGACHAT_CREDENTIALS. Создайте .env по образцу .env.example."
            )
        scope = os.getenv("GIGACHAT_SCOPE", DEFAULT_SCOPE)
        verify_ssl = _env_bool("GIGACHAT_VERIFY_SSL", default=False)

        self.model = model or os.getenv("GIGACHAT_MODEL", DEFAULT_MODEL)
        self.max_tokens = max_tokens
        self._client = GigaChat(
            credentials=credentials,
            scope=scope,
            model=self.model,
            verify_ssl_certs=verify_ssl,
        )

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(
            (httpx.HTTPError, httpx.TimeoutException, ResponseError)
        ),
        reraise=True,
    )
    def complete(
        self,
        system: str,
        user: str,
        *,
        cache_system: bool = True,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Один вызов GigaChat. Возвращает текст ответа ассистента.

        cache_system оставлен в сигнатуре для обратной совместимости,
        но у GigaChat нет аналога Anthropic ephemeral cache — параметр игнорируется.
        """
        del cache_system  # no-op для GigaChat

        payload = Chat(
            messages=[
                Messages(role=MessagesRole.SYSTEM, content=system),
                Messages(role=MessagesRole.USER, content=user),
            ],
            model=self.model,
            max_tokens=max_tokens or self.max_tokens,
            temperature=0.2,
        )

        try:
            response = self._client.chat(payload)
        except AuthenticationError:
            raise

        usage = getattr(response, "usage", None)
        if usage is not None:
            logger.debug(
                "GigaChat usage: prompt={pt}, completion={ct}, total={tt}",
                pt=getattr(usage, "prompt_tokens", "?"),
                ct=getattr(usage, "completion_tokens", "?"),
                tt=getattr(usage, "total_tokens", "?"),
            )

        if not response.choices:
            raise RuntimeError("GigaChat вернул пустой ответ без choices")
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("GigaChat вернул пустое сообщение")
        return content
