"""Обёртка над anthropic SDK с retry и prompt caching.

Системный промпт во всех вызовах в рамках пакета один и тот же —
разворачиваем его в кэшируемый блок (ephemeral cache_control), чтобы
второй и последующие фермеры в пакете обходились в ~10% стоимости
оригинального системного промпта.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from anthropic import Anthropic, APIConnectionError, APITimeoutError, RateLimitError
from dotenv import load_dotenv
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

load_dotenv()


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

DEFAULT_MODEL = "claude-sonnet-4-6"


def load_prompt(name: str) -> str:
    """Прочитать промпт из prompts/<name>.md."""
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


class AIClient:
    """Тонкая обёртка вокруг Anthropic.messages.create.

    Использование:
        client = AIClient()
        text = client.complete(system="...", user="...")
    """

    def __init__(self, model: Optional[str] = None, max_tokens: int = 2048) -> None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "Не задан ANTHROPIC_API_KEY. Создайте .env по образцу .env.example."
            )
        self._client = Anthropic(api_key=api_key)
        self.model = model or os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)
        self.max_tokens = max_tokens

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(
            (RateLimitError, APITimeoutError, APIConnectionError)
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
        """Один вызов Claude. Возвращает текст первого content-блока ответа."""
        if cache_system:
            system_param = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_param = system

        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens or self.max_tokens,
            system=system_param,
            messages=[{"role": "user", "content": user}],
        )

        usage = getattr(response, "usage", None)
        if usage is not None:
            logger.debug(
                "Claude usage: input={in_t}, output={out_t}, "
                "cache_read={cr}, cache_create={cc}",
                in_t=getattr(usage, "input_tokens", "?"),
                out_t=getattr(usage, "output_tokens", "?"),
                cr=getattr(usage, "cache_read_input_tokens", 0),
                cc=getattr(usage, "cache_creation_input_tokens", 0),
            )

        for block in response.content:
            if getattr(block, "type", None) == "text":
                return block.text
        raise RuntimeError("Claude вернул пустой ответ без text-блока")
