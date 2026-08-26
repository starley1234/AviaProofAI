"""LLM-проверка чертежа (OpenAI-совместимый endpoint: gemma/vLLM/Ollama).

Промпт: текст чертежа + требования -> JSON-список замечаний.
Если endpoint не настроен (MODEL_BASE_URL пуст) — эвристический фолбэк:
числовые ограничения требований сверяются с числами из текста чертежа.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from drawing_check.config import settings

# Эталонные требования для проверки чертежа (можно подключить требования
# из AviaProofAI — см. docs/drawing_check.md)
REQUIREMENTS: list[dict] = [
    {"id": "REQ-1201", "text": "Максимальное усилие на органе управления торможением, "
                               "необходимое для создания полного тормозного давления, "
                               "должно быть не более 100 Н."},
    {"id": "REQ-1301", "text": "Минимальное усилие на органе управления торможением, "
                               "необходимое для создания полного тормозного давления, "
                               "должно быть не менее 150 Н."},
    {"id": "REQ-1203", "text": "Тормозной путь самолёта при прерванном взлёте "
                               "не должен превышать 1500 м."},
    {"id": "REQ-1302", "text": "Стояночный тормоз должен удерживать самолёт с максимальной "
                               "взлётной массой на уклоне 10 процентов."},
]

CHECK_SYSTEM = (
    "Ты — инженер по контролю чертежей авиационной техники. Сверяешь параметры "
    "чертежа с требованиями. Отвечай строго JSON-объектом, без пояснений."
)

CHECK_USER = """Сверь параметры ЧЕРТЕЖА с ТРЕБОВАНИЯМИ. Найди несоответствия:
числовые параметры вне допустимых диапазонов, отсутствующие обязательные элементы,
неоднозначные обозначения. Если несоответствий нет — верни пустой список.

ЧЕРТЁЖ:
{drawing_text}

ТРЕБОВАНИЯ:
{requirements}

Верни JSON:
{{
  "findings": [
    {{
      "requirement_id": "REQ-1201",
      "severity": "critical" | "warning" | "info",
      "title": "кратко что не так",
      "detail": "подробно: значение на чертеже vs требование",
      "suggestion": "как исправить"
    }}
  ]
}}"""


class CheckError(Exception):
    pass


def _num_constraints(req_text: str) -> list[dict]:
    """Числовые ограничения требования: {'kind': max|min, 'value': float, 'unit': str}."""
    out = []
    unit = r"(?:Н|кгс|кН|м|мм|км|с|мин|ч|кг|т|%|посад\w*|цикл\w*)"
    for kind, pat in (("max", r"не\s+более\s+"), ("min", r"не\s+менее\s+")):
        for m in re.finditer(pat + rf"(\d+(?:[.,]\d+)?)\s*({unit})", req_text, re.IGNORECASE):
            out.append({"kind": kind, "value": float(m.group(1).replace(",", ".")),
                        "unit": m.group(2)})
    return out


def _drawing_numbers(text: str) -> list[tuple[float, str]]:
    """(число, единица) из текста чертежа."""
    unit = r"(?:Н|кгс|кН|м|мм|км|с|мин|ч|кг|т|%|посад\w*|цикл\w*)"
    return [(float(m.group(1).replace(",", ".")), m.group(2))
            for m in re.finditer(rf"(\d+(?:[.,]\d+)?)\s*({unit})", text)]


def heuristic_check(drawing_text: str) -> list[dict]:
    """Эвристический фолбэк: числа чертежа против числовых ограничений требований."""
    findings: list[dict] = []
    nums = _drawing_numbers(drawing_text)
    for req in REQUIREMENTS:
        for c in _num_constraints(req["text"]):
            for value, unit in nums:
                if unit != c["unit"]:
                    continue
                if c["kind"] == "max" and value > c["value"]:
                    findings.append({
                        "requirement_id": req["id"],
                        "severity": "critical",
                        "title": f"Параметр превышает допустимый максимум ({c['value']} {unit})",
                        "detail": f"На чертеже: {value:g} {unit}; требование: не более "
                                  f"{c['value']:g} {unit}. Текст требования: {req['text'][:120]}…",
                        "suggestion": f"Уменьшить параметр до {c['value']:g} {unit} "
                                      "или согласовать отклонение с разработчиком требования",
                    })
                elif c["kind"] == "min" and value < c["value"]:
                    findings.append({
                        "requirement_id": req["id"],
                        "severity": "critical",
                        "title": f"Параметр ниже допустимого минимума ({c['value']} {unit})",
                        "detail": f"На чертеже: {value:g} {unit}; требование: не менее "
                                  f"{c['value']:g} {unit}. Текст требования: {req['text'][:120]}…",
                        "suggestion": f"Увеличить параметр до {c['value']:g} {unit} "
                                      "или согласовать отклонение",
                    })
    return findings


def llm_check(drawing_text: str, model_name: str | None = None,
              base_url: str | None = None, api_key: str | None = None) -> list[dict]:
    """Проверка через LLM (OpenAI-совместимый). Бросает CheckError при сбое."""
    base = (base_url or settings.model_base_url).rstrip("/")
    reqs_txt = "\n".join(f"- {r['id']}: {r['text']}" for r in REQUIREMENTS)
    prompt = CHECK_USER.format(drawing_text=drawing_text or "(текст не распознан)", requirements=reqs_txt)
    payload = {
        "model": model_name or settings.model_name,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": CHECK_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    headers = {"Content-Type": "application/json"}
    if api_key or settings.model_api_key:
        headers["Authorization"] = f"Bearer {api_key or settings.model_api_key}"
    try:
        resp = httpx.post(f"{base}/chat/completions", json=payload, headers=headers,
                          timeout=settings.model_timeout)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        raise CheckError(f"LLM endpoint {base}: {e}") from e
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        data = json.loads(match.group(0)) if match else {}
    findings = data.get("findings", []) if isinstance(data, dict) else []
    return [f for f in findings if isinstance(f, dict) and f.get("requirement_id")]


def run_llm_or_heuristic(drawing_text: str, engine: str | None = None) -> tuple[list[dict], str]:
    """Основная точка входа: LLM при наличии endpoint, иначе эвристики.

    Возвращает (findings, engine_used). При сбое LLM — лог + фолбэк на эвристики.
    """
    engine = engine or ("llm" if settings.llm_enabled else "heuristic")
    if engine == "llm":
        try:
            return llm_check(drawing_text), "llm"
        except CheckError as e:
            from drawing_check.logs import log_store
            log_store.warn(f"LLM недоступен, фолбэк на эвристики: {e}")
            return heuristic_check(drawing_text), "heuristic"
    return heuristic_check(drawing_text), "heuristic"
