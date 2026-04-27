"""Валидация российских реквизитов: ИНН, КПП, БИК, расчётный счёт.

Чистые функции без сети. Возвращают bool. Человеко-читаемые сообщения
об ошибках формирует слой Pydantic-валидаторов в models.py.
"""

from __future__ import annotations

import re

_INN10_WEIGHTS = (2, 4, 10, 3, 5, 9, 4, 6, 8, 0)
_INN12_WEIGHTS_11 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8, 0)
_INN12_WEIGHTS_12 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8, 0)

_ACCOUNT_WEIGHTS = (7, 1, 3, 7, 1, 3, 7, 1, 3, 7, 1, 3, 7, 1, 3, 7, 1, 3, 7, 1, 3, 7, 1)

_KPP_RE = re.compile(r"^\d{4}[\dA-Z]{2}\d{3}$")


def _checksum(digits: list[int], weights: tuple[int, ...]) -> int:
    return sum(d * w for d, w in zip(digits, weights)) % 11 % 10


def validate_inn(inn: str) -> bool:
    """ИНН: 10 цифр (юрлицо) или 12 цифр (ИП/физлицо) с контрольной суммой ФНС."""
    if not inn or not inn.isdigit():
        return False
    digits = [int(c) for c in inn]
    if len(digits) == 10:
        return _checksum(digits[:10], _INN10_WEIGHTS) == digits[9]
    if len(digits) == 12:
        return (
            _checksum(digits[:11], _INN12_WEIGHTS_11) == digits[10]
            and _checksum(digits[:12], _INN12_WEIGHTS_12) == digits[11]
        )
    return False


def validate_kpp(kpp: str) -> bool:
    """КПП: 9 символов формата NNNNRRPPP (где RR — 2 цифры или 2 буквы A-Z)."""
    if not kpp:
        return False
    return bool(_KPP_RE.match(kpp))


def validate_bik(bik: str) -> bool:
    """БИК: 9 цифр, для банков РФ начинается с '04'."""
    if not bik or not bik.isdigit() or len(bik) != 9:
        return False
    return bik.startswith("04")


def validate_account_number(account: str, bik: str) -> bool:
    """Расчётный счёт: 20 цифр + контрольная сумма по алгоритму ЦБ РФ.

    Алгоритм: к последним 3 цифрам БИК приписывается 20-значный номер счёта.
    Каждая из 23 цифр умножается на свой вес из последовательности
    (7,1,3,7,1,3,...). Сумма произведений mod 10 должна быть 0.
    """
    if not account or not account.isdigit() or len(account) != 20:
        return False
    if not validate_bik(bik):
        return False
    full = bik[-3:] + account
    total = sum(int(d) * w for d, w in zip(full, _ACCOUNT_WEIGHTS))
    return total % 10 == 0


def validate_correspondent_account(corr_account: str, bik: str) -> bool:
    """Корреспондентский счёт: 20 цифр + контрольная сумма (своя свёртка).

    Для корсчёта спереди приписывается '0' + 5-6 цифры БИК (positions 4..6 0-indexed),
    а не последние 3 цифры БИК.
    """
    if not corr_account or not corr_account.isdigit() or len(corr_account) != 20:
        return False
    if not validate_bik(bik):
        return False
    full = "0" + bik[4:6] + corr_account
    total = sum(int(d) * w for d, w in zip(full, _ACCOUNT_WEIGHTS))
    return total % 10 == 0
