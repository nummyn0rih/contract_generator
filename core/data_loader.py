"""Загрузка базы фермеров из xlsx/csv.

Каждая строка обрабатывается отдельно: ошибка валидации одной строки
не валит загрузку остальных. Возвращается список Farmer и список
GenerationResult с data_error для проблемных строк.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd
from loguru import logger
from pydantic import ValidationError

from core.models import Farmer, GenerationResult


COLUMN_MAP: dict[str, str] = {
    "ФИО": "full_name",
    "ИНН": "inn",
    "КПП": "kpp",
    "Банк": "bank_name",
    "БИК": "bank_bik",
    "Р/счёт": "bank_account",
    "Р_СЧЕТ": "bank_account",
    "К/счёт": "correspondent_account",
    "К_СЧЕТ": "correspondent_account",
    "Регион": "region",
    "Культура": "culture",
    "Объём, т": "volume_tons",
    "Объём": "volume_tons",
    "Цена за тонну, руб": "price_per_ton",
    "Цена за тонну": "price_per_ton",
    "Дата договора": "contract_date",
    "Дата": "contract_date",
    "Сезон": "season_year",
    "Особые условия": "extra_conditions",
}

REQUIRED_FIELDS: set[str] = {
    "full_name",
    "inn",
    "bank_name",
    "bank_bik",
    "bank_account",
    "correspondent_account",
    "region",
    "culture",
    "volume_tons",
    "price_per_ton",
    "contract_date",
    "season_year",
}


def _read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=str)  # читаем всё строками, кастуем в Pydantic
    if suffix == ".csv":
        return pd.read_csv(path, dtype=str)
    raise ValueError(f"Неподдерживаемый формат: {suffix}. Ожидается .xlsx или .csv")


def _normalize_row(row: pd.Series) -> dict:
    """Переименовать колонки и нормализовать значения."""
    out: dict = {}
    for col, value in row.items():
        key = COLUMN_MAP.get(str(col).strip(), str(col).strip())
        if pd.isna(value) or (isinstance(value, str) and value.strip() == ""):
            out[key] = None
        else:
            out[key] = value.strip() if isinstance(value, str) else value
    return out


def _format_validation_error(exc: ValidationError) -> list[str]:
    msgs: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", []))
        msgs.append(f"{loc}: {err.get('msg', '')}")
    return msgs


def load_farmers(path: Path | str) -> tuple[list[Farmer], list[GenerationResult]]:
    """Загрузить базу фермеров. Возвращает (валидные фермеры, отчёты об ошибках)."""
    path = Path(path)
    df = _read_table(path)
    logger.info(f"Загружено {len(df)} строк из {path.name}")

    farmers: list[Farmer] = []
    errors: list[GenerationResult] = []

    for idx, row in df.iterrows():
        data = _normalize_row(row)
        label = str(data.get("full_name") or f"строка {idx + 2}")

        missing = [f for f in REQUIRED_FIELDS if data.get(f) in (None, "")]
        if missing:
            errors.append(
                GenerationResult(
                    farmer_label=label,
                    status="data_error",
                    errors=[f"не заполнены обязательные поля: {', '.join(missing)}"],
                )
            )
            continue

        try:
            farmer = Farmer.model_validate(data)
            farmers.append(farmer)
        except ValidationError as exc:
            logger.warning(f"Строка {idx + 2} ({label}) — ошибки валидации")
            errors.append(
                GenerationResult(
                    farmer_label=label,
                    status="data_error",
                    errors=_format_validation_error(exc),
                )
            )

    logger.info(f"Валидных фермеров: {len(farmers)}, с ошибками: {len(errors)}")
    return farmers, errors


def preview_dataframe(path: Path | str) -> pd.DataFrame:
    """Сырое чтение для отображения превью в UI."""
    return _read_table(Path(path))
