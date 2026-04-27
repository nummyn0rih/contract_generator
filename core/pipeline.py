"""Оркестратор генерации пакета договоров.

Загружает фермеров → для каждого: AI-генерация пунктов → заполнение
шаблона → AI-проверка → формирование GenerationResult.

Ошибка на одном фермере НЕ должна останавливать обработку остальных.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from core import ai_generator, ai_validator
from core.ai_client import AIClient
from core.data_loader import load_farmers
from core.docx_filler import fill_template
from core.models import Farmer, GenerationResult


_FILENAME_SAFE_RE = re.compile(r"[^\w\-. ]+", re.UNICODE)


def _safe_filename(name: str) -> str:
    cleaned = _FILENAME_SAFE_RE.sub("_", name).strip().replace(" ", "_")
    return cleaned[:80] or "farmer"


def _output_path(output_dir: Path, farmer: Farmer) -> Path:
    today = date.today().strftime("%Y-%m-%d")
    return output_dir / f"Договор_{_safe_filename(farmer.full_name)}_{today}.docx"


def _setup_run_logger(logs_dir: Path) -> int:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"run_{datetime.now():%Y-%m-%d_%H-%M-%S}.log"
    return logger.add(log_path, encoding="utf-8", rotation="10 MB", retention=10)


def run_pipeline(
    template_path: Path | str,
    farmers_path: Path | str,
    output_dir: Path | str,
    *,
    logs_dir: Path | str = "logs",
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    client: Optional[AIClient] = None,
) -> list[GenerationResult]:
    """Запустить пакетную генерацию.

    Args:
        template_path: путь к .docx-шаблону.
        farmers_path: путь к xlsx/csv с фермерами.
        output_dir: куда складывать готовые договоры.
        logs_dir: куда писать loguru-логи.
        progress_callback: (i, total, message) — для UI прогресс-бара.
        client: предсозданный AIClient (для тестов); по умолчанию создаётся новый.

    Returns:
        Список GenerationResult (по одному на каждую строку базы).
    """
    template_path = Path(template_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    handler_id = _setup_run_logger(Path(logs_dir))
    logger.info(f"Запуск пайплайна. Шаблон={template_path.name}, база={Path(farmers_path).name}")

    try:
        farmers, error_results = load_farmers(farmers_path)
        results: list[GenerationResult] = list(error_results)

        if not farmers:
            logger.warning("Нет валидных фермеров для генерации")
            return results

        client = client or AIClient()
        total = len(farmers)

        for idx, farmer in enumerate(farmers, start=1):
            if progress_callback:
                progress_callback(idx, total, farmer.full_name)
            logger.info(f"[{idx}/{total}] {farmer.full_name}")

            try:
                clauses = ai_generator.generate_individual_clauses(farmer, client)
            except Exception as exc:
                logger.exception(f"Ошибка AI-генерации для {farmer.full_name}")
                results.append(
                    GenerationResult(
                        farmer_label=farmer.full_name,
                        farmer=farmer,
                        status="generation_error",
                        errors=[f"AI-генерация пунктов упала: {exc}"],
                    )
                )
                continue

            try:
                ctx = farmer.to_template_context(individual_clauses=clauses)
                out_path = _output_path(output_dir, farmer)
                remaining = fill_template(template_path, ctx, out_path)
            except Exception as exc:
                logger.exception(f"Ошибка заполнения шаблона для {farmer.full_name}")
                results.append(
                    GenerationResult(
                        farmer_label=farmer.full_name,
                        farmer=farmer,
                        status="generation_error",
                        errors=[f"Заполнение шаблона упало: {exc}"],
                        ai_clauses=clauses,
                    )
                )
                continue

            warnings: list[str] = []
            if remaining:
                warnings.append(f"остались незаполненные плейсхолдеры: {', '.join(remaining)}")

            try:
                review = ai_validator.review_contract(out_path, farmer, client)
                warnings.extend(review.issues)
                ai_status = review.status
            except Exception as exc:
                logger.exception(f"Ошибка AI-проверки для {farmer.full_name}")
                warnings.append(f"AI-проверка упала: {exc}")
                ai_status = "warnings"

            status = "ok" if ai_status == "ok" and not warnings else "ai_warnings"
            results.append(
                GenerationResult(
                    farmer_label=farmer.full_name,
                    farmer=farmer,
                    status=status,
                    output_path=out_path,
                    ai_warnings=warnings,
                    ai_clauses=clauses,
                )
            )

        ok_count = sum(1 for r in results if r.status == "ok")
        logger.info(f"Готово. Успешно: {ok_count}/{len(results)}")
        return results

    finally:
        logger.remove(handler_id)
