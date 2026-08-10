"""RuleBasedClient — эвристический «LLM» без сети.

Нужен, чтобы сервис работал даже без внешней модели (демо, тесты, офлайн).
Логика намеренно простая и детерминированная; при подключении реального LLM
агенты используют его, а этот движок остаётся фолбэком.

Схемы JSON-ответов совпадают с промптами (prompts.py).
"""
from __future__ import annotations

import json
import re

from app.services.llm.base import LLMClient

# ─────────────── общие лексические инструменты ───────────────
VAGUE_PATTERNS: list[tuple[str, str, str]] = [
    # (regex, severity, описание)
    (r"достаточно\s+\w+", "warning", "Непроверяемое слово «достаточно …» — нет количественного критерия"),
    (r"нормальных условиях", "warning", "«Нормальные условия» не определены количественно (температура, давление)"),
    (r"при необходимости", "warning", "«При необходимости» — условие не определено"),
    (r"по возможности", "warning", "«По возможности» — условие не проверяемо"),
    (r"должным образом", "warning", "«Должным образом» — критерий не определён"),
    (r"и\s*т\.?\s*д\.?|и\s*т\.?\s*п\.?", "warning", "Открытый список «и т.д.» — требование неполно"),
    (r"соответствующий", "warning", "«Соответствующий» — не указано, чему именно"),
    (r"\bappropriate\b|\bsuitable\b|\betc\.?\b|\bas required\b", "warning", "Vague wording (EN)"),
]

FIXES: dict[str, str] = {
    "достаточно надёжной": "со средней наработкой на отказ не менее 10 000 ч",
    "достаточно надежной": "со средней наработкой на отказ не менее 10 000 ч",
    "нормальных условиях эксплуатации": "при температуре от −55 до +70 °C и давлении от 570 до 1013 гПа",
    "при необходимости": "при отказе основного канала",
    "по возможности": "при отсутствии ограничений по массе",
}

REQ_VERB = r"(?:должн\w+|shall|must|обязан\w*)"
NUMBER = r"\d+(?:[.,]\d+)?"


def _norm(text: str) -> str:
    """Нормализация для сравнения: нижний регистр, слова."""
    return re.sub(r"[^\w\sа-яёa-z-]", " ", text.lower(), flags=re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {t for t in _norm(text).split() if len(t) > 2}


def jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _constraints(text: str) -> list[dict]:
    """Числовые ограничения: {'kind': 'max'|'min', 'value': float, 'unit': str}."""
    out = []
    unit = r"(?:Н|кгс|кН|м|мм|км|с|мин|ч|кг|т|%|градус\w*|°C|посад\w*|цикл\w*)"
    # «не более X unit» / «не менее X unit» / «до X unit» / «от X unit»
    for kind, pat in (("max", r"не\s+более\s+"), ("min", r"не\s+менее\s+")):
        for m in re.finditer(pat + rf"({NUMBER})\s*({unit})", text, re.IGNORECASE):
            out.append({"kind": kind, "value": float(m.group(1).replace(",", ".")),
                        "unit": m.group(2)})
    return out


class RuleBasedClient(LLMClient):
    name = "rule-based"

    def complete_json(self, system: str, user: str) -> dict:
        # система не используется: эвристики смотрят только на user-часть
        return self._route(user)

    def _route(self, user: str) -> dict:
        if "Проверь требование" in user or "атомарность" in user.lower():
            text = self._extract(user, "ТРЕБОВАНИЕ:", "Верни JSON")
            return self.audit(text)
        if "Сравни два требования" in user:
            a = self._extract(user, "ТРЕБОВАНИЕ A", "ТРЕБОВАНИЕ B")
            b = self._extract(user, "ТРЕБОВАНИЕ B", "Верни JSON")
            return self.compare(a, b)
        if "Исправь формулировку" in user:
            text = self._extract(user, "ОРИГИНАЛ:", "ПРОБЛЕМЫ:")
            return self.propose_edit(text)
        if "Анализируешь влияние" in user or "ИЗМЕНЯЕМОЕ ТРЕБОВАНИЕ" in user:
            text = self._extract(user, "ИЗМЕНЯЕМОЕ ТРЕБОВАНИЕ", "СВЯЗАННЫЕ ТРЕБОВАНИЯ")
            related = self._extract(user, "СВЯЗАННЫЕ ТРЕБОВАНИЯ", "Верни JSON")
            return self.impact(text, related)
        return {"error": "неизвестный запрос"}

    @staticmethod
    def _extract(user: str, start: str, end: str) -> str:
        i = user.find(start)
        if i < 0:
            return user
        i += len(start)
        j = user.find(end, i)
        return user[i:j if j > 0 else None].strip()

    # ─────────────── аудит качества (DO-178C) ───────────────
    def audit(self, text: str) -> dict:
        issues: list[dict] = []
        low = text.lower()
        for pattern, severity, desc in VAGUE_PATTERNS:
            if re.search(pattern, low):
                issues.append({"type": "verifiability", "severity": severity,
                               "description": desc,
                               "suggestion": "Заменить неопределённую формулировку измеримым критерием"})
        # атомарность: несколько глаголов-требований
        verbs = len(re.findall(REQ_VERB, low))
        if verbs > 1:
            issues.append({"type": "atomicity", "severity": "warning",
                           "description": f"Требование содержит {verbs} утверждения-глагола — "
                                          "должно быть одно (DO-178C: атомарность)",
                           "suggestion": "Разбить на отдельные требования"})
        if re.search(r"и/или|\band/or\b", low):
            issues.append({"type": "atomicity", "severity": "warning",
                           "description": "«И/или» — два альтернативных утверждения в одном требовании",
                           "suggestion": "Разбить на два требования"})
        # непротиворечивость внутри одного требования
        cons = _constraints(text)
        for c1 in cons:
            for c2 in cons:
                if c1["kind"] != c2["kind"] and c1["unit"] == c2["unit"] and \
                        ((c1["kind"] == "max" and c2["kind"] == "min" and c2["value"] > c1["value"]) or
                         (c1["kind"] == "min" and c2["kind"] == "max" and c1["value"] > c2["value"])):
                    issues.append({"type": "consistency", "severity": "critical",
                                   "description": f"Внутреннее противоречие ограничений: "
                                                  f"{c1['value']} и {c2['value']} {c1['unit']}",
                                   "suggestion": "Согласовать границы диапазона"})
        score = max(0, 100 - 25 * len(issues))
        return {"score": score, "issues": issues}

    # ─────────────── сравнение пары требований ───────────────
    def compare(self, a: str, b: str) -> dict:
        # 1) непохожие тексты — не противоречат (нет общего контекста)
        if jaccard(a, b) < 0.3:
            return {"contradiction": False, "type": "none",
                    "explanation": "Требования не связаны по содержанию", "suggestion": ""}
        # 2) числовые диапазоны: max(X) < min(Y) при одной единице — противоречие
        ca, cb = _constraints(a), _constraints(b)
        for x in ca:
            for y in cb:
                if x["unit"] == y["unit"] and x["kind"] != y["kind"]:
                    maxv = x if x["kind"] == "max" else y
                    minv = y if y["kind"] == "min" else x
                    if minv["value"] > maxv["value"]:
                        return {
                            "contradiction": True, "type": "numeric_range",
                            "explanation": (f"Требование A: «не более {maxv['value']} {maxv['unit']}», "
                                            f"требование B: «не менее {minv['value']} {minv['unit']}» — "
                                            "диапазон допустимых значений пуст"),
                            "suggestion": (f"Согласовать границы: выбрать единое значение в диапазоне "
                                           f"от {maxv['value']} до {minv['value']} {maxv['unit']} "
                                           "и зафиксировать его в обоих разделах"),
                        }
        # 3) полярность: «должен X» vs «не должен X» (отрицание может стоять перед глаголом)
        def clauses(text: str) -> list[tuple[bool, str]]:
            return [(bool(m.group(1)), m.group(2).strip())
                    for m in re.finditer(rf"(не\s+)?{REQ_VERB}\s+(.+?)[.;]", text.lower())]

        for neg_a, va in clauses(a):
            for neg_b, vb in clauses(b):
                if va == vb and neg_a != neg_b:
                    label_a = f"{'не ' if neg_a else ''}должен {va}"
                    label_b = f"{'не ' if neg_b else ''}должен {vb}"
                    return {"contradiction": True, "type": "polarity",
                            "explanation": f"Требование A: «{label_a}», требование B: «{label_b}» — "
                                           "противоположные предписания",
                            "suggestion": "Устранить противоположные требования, согласовать с вышестоящим"}
        return {"contradiction": False, "type": "none",
                "explanation": "Противоречий не обнаружено", "suggestion": ""}

    # ─────────────── предложение правки ───────────────
    def propose_edit(self, text: str, issues: list[dict] | None = None) -> dict:
        issues = issues or self.audit(text).get("issues", [])
        new_text, rationale = text, []
        for iss in issues:
            if iss["type"] == "verifiability":
                fixed = False
                for phrase, replacement in FIXES.items():
                    if phrase in new_text.lower():
                        # сохраняем регистр первого слова оригинала
                        idx = new_text.lower().find(phrase)
                        orig = new_text[idx:idx + len(phrase)]
                        new_text = new_text[:idx] + replacement + new_text[idx + len(phrase):]
                        rationale.append(f"«{orig}» заменено на измеримый критерий «{replacement}»")
                        fixed = True
                        break
                if not fixed:
                    rationale.append("Неопределённая формулировка: требуется количественный критерий")
            elif iss["type"] == "atomicity":
                # разбивка после «;»/«.» на клаузы, содержащие глагол-требование
                # (между знаком и глаголом допускается подлежащее: «; система должна ...»)
                split_re = re.compile(rf"(?<=[.;])\s+(?=(?:[а-яёa-z-]+\s){{0,8}}{REQ_VERB})", re.IGNORECASE)
                parts = split_re.split(new_text)
                if len(parts) > 1:
                    new_text = "\n".join(f"{i + 1}. {p.strip()}" for i, p in enumerate(parts))
                    rationale.append(f"Требование разбито на {len(parts)} атомарных утверждений")
        return {"proposed_text": new_text,
                "rationale": "; ".join(rationale) or "Правок не требуется"}

    # ─────────────── impact-анализ ───────────────
    def impact(self, changed_text: str, related_text: str) -> dict:
        """related_text — «uid | текст» по одному на строку (uid без пробелов)."""
        affected = []
        for line in related_text.splitlines():
            line = line.strip()
            uid, sep, rel_text = line.partition("|")
            uid = uid.strip()
            if not sep or not uid or " " in uid:  # мусорные строки пропускаем
                continue
            sim = jaccard(changed_text, rel_text)
            if sim >= 0.25:
                affected.append({"uid": uid,
                                 "impact": f"Изменение затрагивает общие формулировки "
                                           f"(совпадение лексики {sim:.0%}); проверить согласованность"})
            else:
                affected.append({"uid": uid, "impact": "не затронуто"})
        return {"affected": affected}

    def audit_json(self, text: str) -> str:
        return json.dumps(self.audit(text), ensure_ascii=False)
