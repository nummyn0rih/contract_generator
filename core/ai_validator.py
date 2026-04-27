"""AI-проверка готового договора по чек-листу.

Просим Claude вернуть строгий JSON. Если разметка слегка съехала
(например, пришёл markdown-fence), пытаемся аккуратно достать JSON.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from loguru import logger
from pydantic import BaseModel, Field

from core.ai_client import AIClient, load_prompt
from core.docx_filler import extract_text
from core.models import Farmer


_SYSTEM_PROMPT = None
_USER_TEMPLATE = None

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)```", re.DOTALL)


class ValidationReport(BaseModel):
    status: Literal["ok", "warnings"] = "warnings"
    issues: list[str] = Field(default_factory=list)


def _load() -> tuple[str, str]:
    global _SYSTEM_PROMPT, _USER_TEMPLATE
    if _SYSTEM_PROMPT is None:
        _SYSTEM_PROMPT = load_prompt("validator_system.md")
    if _USER_TEMPLATE is None:
        _USER_TEMPLATE = load_prompt("validator_user.md")
    return _SYSTEM_PROMPT, _USER_TEMPLATE


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    fence_match = _JSON_FENCE_RE.search(raw)
    if fence_match:
        raw = fence_match.group(1).strip()
    # На случай если модель добавила пояснения вокруг — берём первый balanced JSON-объект.
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"Не нашёл JSON в ответе модели: {raw[:200]}")
    return json.loads(raw[start : end + 1])


def review_contract(docx_path: Path | str, farmer: Farmer, client: AIClient) -> ValidationReport:
    """Проверить готовый .docx по чек-листу."""
    system, user_template = _load()
    contract_text = extract_text(docx_path)

    user_prompt = user_template.format(
        full_name=farmer.full_name,
        inn=farmer.inn,
        kpp=farmer.kpp or "—",
        bank_name=farmer.bank_name,
        bank_bik=farmer.bank_bik,
        bank_account=farmer.bank_account,
        correspondent_account=farmer.correspondent_account,
        region=farmer.region,
        culture=farmer.culture,
        volume_tons=f"{farmer.volume_tons:.2f}",
        price_per_ton=f"{farmer.price_per_ton:.2f}",
        total_amount=f"{farmer.total_amount:.2f}",
        total_amount_words=farmer.total_amount_words,
        contract_date=farmer.contract_date.strftime("%d.%m.%Y"),
        season_year=farmer.season_year,
        contract_text=contract_text,
    )

    logger.debug(f"AI-валидация договора для {farmer.full_name}")
    raw = client.complete(system=system, user=user_prompt, cache_system=True)

    try:
        data = _extract_json(raw)
        return ValidationReport.model_validate(data)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning(f"Не разобрал JSON от модели для {farmer.full_name}: {exc}")
        return ValidationReport(
            status="warnings",
            issues=[f"AI-ответ не в ожидаемом JSON-формате: {raw[:300]}"],
        )
