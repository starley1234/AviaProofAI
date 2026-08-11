"""UI «Проверка чертежа» (Streamlit, порт 8501).

Запуск: streamlit run drawing_check/ui.py --server.port 8501 --server.address 0.0.0.0

Страницы:
    «Загрузка чертежа» — загрузить чертёж (png/jpg/pdf...) -> кнопка «Запустить проверку»;
    «Логи» — нормальные логи проверок: по шагам (загрузка -> OCR -> LLM -> результат),
             кнопка «Стереть логи» (старые логи больше не мешают), автообновление.
    «Диагностика» — какой адрес API использует UI (внутренний backend:8502 vs
             внешний localhost:8502) + проверка соединения с обоими.
"""
from __future__ import annotations

import os
import time

import httpx
import streamlit as st

from drawing_check.config import settings

st.set_page_config(page_title="Проверка чертежа", page_icon="📐", layout="wide")

LEVEL_COLORS = {"DEBUG": "gray", "INFO": "green", "WARN": "orange", "ERROR": "red"}


def _api_url() -> str:
    return os.getenv("API_URL") or settings.ui_api_url


def _call_api(method: str, path: str, **kw):
    url = _api_url().rstrip("/") + path
    resp = getattr(httpx, method)(url, timeout=120, **kw)
    resp.raise_for_status()
    return resp.json()


# ─────────────────────────────── сайдбар ───────────────────────────────
with st.sidebar:
    st.title("📐 Проверка чертежа")
    st.caption(f"API: `{_api_url()}`")

    if st.button("🔄 Проверить соединение с API", use_container_width=True):
        for label, url in (("внешний (браузер)", settings.api_url_external),
                           ("внутренний (docker)", settings.api_url_internal)):
            try:
                r = httpx.get(url.rstrip("/") + "/health", timeout=5)
                st.success(f"{label}: {url} — HTTP {r.status_code}")
            except Exception as e:
                st.error(f"{label}: {url} — НЕДОСТУПЕН: {type(e).__name__}: {e}")
        st.caption("Если внешний доступен, а внутренний нет — это нормально: "
                   "имя `backend` резолвится только внутри docker-сети.")

    if st.button("🧹 Стереть логи", use_container_width=True):
        try:
            res = _call_api("post", "/api/logs/clear")
            st.success(f"Стёрто записей: {res['erased']}")
        except Exception as e:
            st.error(f"Не удалось стереть логи: {e}")

# ─────────────────────────────── вкладки ───────────────────────────────
tab_check, tab_logs, tab_diag = st.tabs(["Загрузка чертежа", "Логи", "Диагностика"])

# ───────────── Загрузка чертежа -> запустить проверку ─────────────
with tab_check:
    st.subheader("Загрузка чертежа")
    uploaded = st.file_uploader(
        "Выберите чертёж (PNG/JPG/PDF/TIFF...)",
        type=["png", "jpg", "jpeg", "bmp", "webp", "pdf", "tif", "tiff"])

    with st.expander("OCR не справился? Вставьте текст чертежа вручную"):
        text_override = st.text_area("Текст с чертежа", height=120)

    run = st.button("🚀 Запустить проверку", type="primary", use_container_width=True)

    if run:
        if uploaded is None and not (text_override or "").strip():
            st.error("Загрузите чертёж или вставьте текст.")
        else:
            files = {"file": (uploaded.name if uploaded else "manual.txt",
                              uploaded.getvalue() if uploaded else b"\x89PNG\r\n\x1a\n",
                              "image/png" if uploaded else "text/plain")}
            data = {"text": text_override or ""}
            try:
                resp = httpx.post(_api_url().rstrip("/") + "/api/check",
                                  files=files, data=data, timeout=180)
                body = resp.json()
            except Exception as e:
                st.error(f"Ошибка вызова API ({_api_url()}): {type(e).__name__}: {e}")
                st.caption("Проверьте вкладку «Диагностика» — возможно, UI обращается "
                           "к внутреннему адресу backend:8502, недоступному из браузера.")
            else:
                if resp.status_code >= 400:
                    st.error(f"API вернул {resp.status_code}: {body.get('detail', body)}")
                    st.caption("Подробности — во вкладке «Логи».")
                else:
                    st.success(f"Проверка завершена за {body['duration_ms']} мс, "
                               f"движок: {body['engine']}, замечаний: {len(body['findings'])}")
                    if not body["findings"]:
                        st.info("Несоответствий требованиям не обнаружено.")
                    for f in body["findings"]:
                        color = {"critical": "🔴", "warning": "🟠", "info": "🔵"}.get(
                            f.get("severity", "info"), "⚪")
                        with st.container(border=True):
                            st.markdown(f"**{color} {f.get('requirement_id')} — {f.get('title')}** "
                                        f"`[{f.get('severity')}]`")
                            st.write(f.get("detail", ""))
                            st.caption(f"💡 {f.get('suggestion', '')}")

# ───────────── Логи ─────────────
with tab_logs:
    st.subheader("Логи проверок")
    col1, col2, col3 = st.columns([1, 1, 3])
    with col1:
        limit = st.selectbox("Сколько строк", [50, 200, 500, 1000], index=1)
    with col2:
        auto = st.checkbox("Автообновление (3 c)", value=True)
    with col3:
        st.caption("Каждый шаг проверки логируется: загрузка → OCR → LLM → результат. "
                   "Ошибки — с текстом исключения, чтобы было видно, где застряло.")

    try:
        data = _call_api("get", f"/api/logs?limit={limit}")
        logs = data["logs"]
    except Exception as e:
        st.error(f"Не удалось получить логи с {_api_url()}: {e}")
        logs = []

    for rec in logs:
        ts = rec.get("ts", "")
        lvl = rec.get("level", "INFO")
        msg = rec.get("msg", "")
        extra = {k: v for k, v in rec.items() if k not in ("ts", "level", "msg") and v not in ("", None)}
        suffix = " · " + " · ".join(f"{k}={v}" for k, v in extra.items()) if extra else ""
        st.markdown(f":{LEVEL_COLORS.get(lvl, 'gray')}[`{ts[11:23]}`] **{lvl}** {msg} `{suffix}`")

    if not logs:
        st.caption("Логов пока нет — запустите проверку.")

    if auto:
        time.sleep(3)
        st.rerun()

# ───────────── Диагностика ─────────────
with tab_diag:
    st.subheader("Диагностика: порты и адреса")
    try:
        info = _call_api("get", "/api/info")
    except Exception as e:
        st.error(f"API недоступен ({_api_url()}): {e}")
        st.stop()
    st.json(info)

    st.markdown("**Как понять, в чём дело (типичные причины):**")
    st.markdown(
        "- UI обращается к **внутреннему** адресу `http://backend:8502` — браузер его не резолвит.\n"
        "  Исправление: используйте внешний `http://localhost:8502` (кнопка соединения выше).\n"
        "- API слушает `127.0.0.1`, а не `0.0.0.0` — проверьте `API_HOST=0.0.0.0`.\n"
        "- В docker-compose не опубликован порт: нужен `ports: [\"8502:8502\"]`.\n"
        "- Модель недоступна: `MODEL_BASE_URL` пуст или неверен — проверка работает "
        "в эвристическом режиме (это видно в логах и в поле `engine` результата).\n"
        "- OCR-движки не установлены — текст не распознаётся (см. `ocr.available` выше); "
        "вставьте текст вручную на вкладке «Загрузка чертежа».")
