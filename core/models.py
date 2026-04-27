"""Pydantic-модели Farmer и GenerationResult.

Farmer — единый источник истины о фермере. Все валидации реквизитов
выполняются здесь, чтобы дальше по пайплайну работать с гарантированно
корректным объектом.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Literal, Optional

from num2words import num2words
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from core import validators as v


def _ru_plural(n: int, one: str, few: str, many: str) -> str:
    """Подбор падежа для русских числительных: 1 рубль / 2 рубля / 5 рублей."""
    n = abs(n) % 100
    if 10 < n < 20:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def amount_to_words_ru(amount: Decimal) -> str:
    """Сумма прописью: '1234.56' -> 'Одна тысяча двести тридцать четыре рубля 56 копеек'."""
    quantized = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    rubles = int(quantized)
    kopecks = int((quantized - rubles) * 100)
    rub_words = num2words(rubles, lang="ru").capitalize()
    rub_suffix = _ru_plural(rubles, "рубль", "рубля", "рублей")
    kop_suffix = _ru_plural(kopecks, "копейка", "копейки", "копеек")
    return f"{rub_words} {rub_suffix} {kopecks:02d} {kop_suffix}"


class Farmer(BaseModel):
    """Карточка фермера. Все реквизиты проверяются на этапе создания модели."""

    model_config = ConfigDict(str_strip_whitespace=True, frozen=False)

    full_name: str = Field(min_length=2, description="ФИО или наименование организации")
    inn: str
    kpp: Optional[str] = None
    bank_name: str = Field(min_length=2)
    bank_bik: str
    bank_account: str
    correspondent_account: str
    region: str = Field(min_length=2)
    culture: str = Field(min_length=2, description="Культура: пшеница, подсолнечник и т.д.")
    volume_tons: Decimal = Field(gt=0, description="Объём поставки, тонн")
    price_per_ton: Decimal = Field(gt=0, description="Цена за тонну, руб.")
    contract_date: date
    season_year: int = Field(ge=2000, le=2100)
    extra_conditions: Optional[str] = None

    @field_validator("inn")
    @classmethod
    def _check_inn(cls, value: str) -> str:
        if not v.validate_inn(value):
            raise ValueError(f"некорректный ИНН: {value!r} (должно быть 10 или 12 цифр + контрольная сумма)")
        return value

    @field_validator("kpp")
    @classmethod
    def _check_kpp(cls, value: Optional[str]) -> Optional[str]:
        if value in (None, ""):
            return None
        if not v.validate_kpp(value):
            raise ValueError(f"некорректный КПП: {value!r} (ожидается 9 символов)")
        return value

    @field_validator("bank_bik")
    @classmethod
    def _check_bik(cls, value: str) -> str:
        if not v.validate_bik(value):
            raise ValueError(f"некорректный БИК: {value!r} (9 цифр, начинается с '04')")
        return value

    @field_validator("bank_account")
    @classmethod
    def _check_account(cls, value: str, info) -> str:
        bik = info.data.get("bank_bik")
        if not bik:
            raise ValueError("нельзя проверить р/счёт без БИК")
        if not v.validate_account_number(value, bik):
            raise ValueError(f"некорректный расчётный счёт: {value!r} (контрольная сумма не сходится)")
        return value

    @field_validator("correspondent_account")
    @classmethod
    def _check_corr(cls, value: str, info) -> str:
        bik = info.data.get("bank_bik")
        if not bik:
            raise ValueError("нельзя проверить корсчёт без БИК")
        if not v.validate_correspondent_account(value, bik):
            raise ValueError(f"некорректный корреспондентский счёт: {value!r}")
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_amount(self) -> Decimal:
        return (self.volume_tons * self.price_per_ton).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_amount_words(self) -> str:
        return amount_to_words_ru(self.total_amount)

    def to_template_context(self, individual_clauses: str = "") -> dict[str, str]:
        """Преобразование в словарь плейсхолдеров для docx_filler."""
        return {
            "ФИО": self.full_name,
            "ИНН": self.inn,
            "КПП": self.kpp or "—",
            "БАНК": self.bank_name,
            "БИК": self.bank_bik,
            "Р_СЧЕТ": self.bank_account,
            "К_СЧЕТ": self.correspondent_account,
            "РЕГИОН": self.region,
            "КУЛЬТУРА": self.culture,
            "ОБЪЕМ": f"{self.volume_tons:.2f}",
            "ЦЕНА_ЗА_ТОННУ": f"{self.price_per_ton:.2f}",
            "СУММА": f"{self.total_amount:.2f}",
            "СУММА_ПРОПИСЬЮ": self.total_amount_words,
            "ДАТА": self.contract_date.strftime("%d.%m.%Y"),
            "СЕЗОН": str(self.season_year),
            "ИНДИВИДУАЛЬНЫЕ_УСЛОВИЯ": individual_clauses,
        }


GenerationStatus = Literal["ok", "data_error", "generation_error", "ai_warnings"]


class GenerationResult(BaseModel):
    """Результат обработки одной строки базы фермеров."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    farmer_label: str
    farmer: Optional[Farmer] = None
    status: GenerationStatus
    output_path: Optional[Path] = None
    errors: list[str] = Field(default_factory=list)
    ai_warnings: list[str] = Field(default_factory=list)
    ai_clauses: Optional[str] = None
