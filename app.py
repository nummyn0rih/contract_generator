"""Streamlit UI для пакетной генерации договоров."""

from __future__ import annotations

import io
import os
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from core.data_loader import preview_dataframe
from core.models import GenerationResult
from core.pipeline import run_pipeline

load_dotenv()

ROOT = Path(__file__).resolve().parent
SAMPLE_TEMPLATE = ROOT / "data" / "template.docx"
SAMPLE_FARMERS = ROOT / "data" / "farmers.xlsx"
OUTPUT_DIR = ROOT / "output"
LOGS_DIR = ROOT / "logs"

st.set_page_config(page_title="Генератор договоров", page_icon="📄", layout="wide")
st.title("📄 Генератор договоров с фермерами")
st.caption("Шаблон + база → пакет .docx с AI-генерацией индивидуальных пунктов и AI-проверкой")


with st.sidebar:
    st.header("Настройки")
    api_key_present = bool(os.getenv("ANTHROPIC_API_KEY"))
    if api_key_present:
        st.success("ANTHROPIC_API_KEY загружен")
    else:
        st.error("ANTHROPIC_API_KEY не найден. Создайте .env по образцу .env.example.")
    st.write(f"**Модель:** `{os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4-6')}`")

    st.divider()
    use_sample = st.checkbox(
        "Использовать пример из data/",
        value=True,
        help="Загрузить демонстрационные template.docx и farmers.xlsx из папки data/",
    )


def _save_uploaded(uploaded_file, suffix: str) -> Path:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(uploaded_file.getbuffer())
    tmp.close()
    return Path(tmp.name)


def _resolve_inputs() -> tuple[Path | None, Path | None]:
    if use_sample:
        if not SAMPLE_TEMPLATE.exists() or not SAMPLE_FARMERS.exists():
            st.warning(
                "В data/ нет примеров. Запустите `python scripts/make_samples.py` "
                "или загрузите свои файлы."
            )
            return None, None
        return SAMPLE_TEMPLATE, SAMPLE_FARMERS

    col1, col2 = st.columns(2)
    with col1:
        tpl = st.file_uploader("Шаблон договора (.docx)", type=["docx"])
    with col2:
        farm = st.file_uploader("База фермеров (.xlsx или .csv)", type=["xlsx", "csv"])
    if not tpl or not farm:
        return None, None
    return _save_uploaded(tpl, ".docx"), _save_uploaded(farm, Path(farm.name).suffix)


template_path, farmers_path = _resolve_inputs()

if farmers_path is not None:
    st.subheader("Превью базы фермеров")
    try:
        df = preview_dataframe(farmers_path)
        st.dataframe(df, width="stretch", hide_index=True)
    except Exception as exc:
        st.error(f"Не удалось прочитать базу: {exc}")


def _result_to_row(r: GenerationResult) -> dict:
    if r.status == "ok":
        status_label = "✅ OK"
    elif r.status == "ai_warnings":
        status_label = "⚠️ Замечания"
    elif r.status == "data_error":
        status_label = "❌ Ошибка данных"
    else:
        status_label = "❌ Ошибка генерации"
    return {
        "Фермер": r.farmer_label,
        "Статус": status_label,
        "Ошибки": "\n".join(r.errors) if r.errors else "",
        "AI-замечания": "\n".join(r.ai_warnings) if r.ai_warnings else "",
        "Файл": r.output_path.name if r.output_path else "",
    }


def _make_zip(results: list[GenerationResult]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in results:
            if r.output_path and r.output_path.exists():
                zf.write(r.output_path, arcname=r.output_path.name)
    return buf.getvalue()


generate_disabled = (
    template_path is None or farmers_path is None or not api_key_present
)

if st.button("🚀 Сгенерировать пакет", type="primary", disabled=generate_disabled):
    progress = st.progress(0.0, text="Подготовка...")
    status_box = st.empty()

    def _progress(i: int, total: int, message: str) -> None:
        progress.progress(i / total, text=f"[{i}/{total}] {message}")

    with st.spinner("Генерация..."):
        try:
            results = run_pipeline(
                template_path=template_path,
                farmers_path=farmers_path,
                output_dir=OUTPUT_DIR,
                logs_dir=LOGS_DIR,
                progress_callback=_progress,
            )
        except Exception as exc:
            st.error(f"Пайплайн упал: {exc}")
            st.stop()

    progress.empty()
    status_box.empty()

    st.session_state["last_results"] = results

if "last_results" in st.session_state:
    results: list[GenerationResult] = st.session_state["last_results"]
    st.subheader("Результаты")

    summary = pd.DataFrame([_result_to_row(r) for r in results])
    st.dataframe(summary, width="stretch", hide_index=True)

    ok_count = sum(1 for r in results if r.status == "ok")
    warn_count = sum(1 for r in results if r.status == "ai_warnings")
    err_count = sum(1 for r in results if r.status in ("data_error", "generation_error"))
    cols = st.columns(3)
    cols[0].metric("Успешно", ok_count)
    cols[1].metric("С замечаниями", warn_count)
    cols[2].metric("С ошибками", err_count)

    successful = [r for r in results if r.output_path]
    if successful:
        zip_bytes = _make_zip(results)
        st.download_button(
            "📦 Скачать все договоры (ZIP)",
            data=zip_bytes,
            file_name="contracts.zip",
            mime="application/zip",
        )

        with st.expander("Скачать поштучно"):
            for r in successful:
                with open(r.output_path, "rb") as f:
                    st.download_button(
                        r.output_path.name,
                        data=f.read(),
                        file_name=r.output_path.name,
                        key=f"dl_{r.farmer_label}",
                    )

    with st.expander("Показать AI-сгенерированные пункты"):
        for r in results:
            if r.ai_clauses:
                st.markdown(f"**{r.farmer_label}**")
                st.text(r.ai_clauses)
                st.divider()
