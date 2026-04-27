"""AI-генерация индивидуальных пунктов договора по карточке фермера."""

from __future__ import annotations

from loguru import logger

from core.ai_client import AIClient, load_prompt
from core.models import Farmer

_SYSTEM_PROMPT = None
_USER_TEMPLATE = None


def _load() -> tuple[str, str]:
    """Ленивая загрузка промптов (даёт UI шанс редактировать без рестарта импортов)."""
    global _SYSTEM_PROMPT, _USER_TEMPLATE
    if _SYSTEM_PROMPT is None:
        _SYSTEM_PROMPT = load_prompt("generator_system.md")
    if _USER_TEMPLATE is None:
        _USER_TEMPLATE = load_prompt("generator_user.md")
    return _SYSTEM_PROMPT, _USER_TEMPLATE


def generate_individual_clauses(farmer: Farmer, client: AIClient) -> str:
    """Сгенерировать блок индивидуальных пунктов под фермера."""
    system, user_template = _load()

    user_prompt = user_template.format(
        full_name=farmer.full_name,
        region=farmer.region,
        culture=farmer.culture,
        volume_tons=f"{farmer.volume_tons:.2f}",
        price_per_ton=f"{farmer.price_per_ton:.2f}",
        contract_date=farmer.contract_date.strftime("%d.%m.%Y"),
        season_year=farmer.season_year,
        extra_conditions=farmer.extra_conditions or "—",
    )

    logger.debug(f"Генерация пунктов для {farmer.full_name}")
    text = client.complete(system=system, user=user_prompt, cache_system=True)
    return text.strip()
