"""LLM-агенты: аудит качества, поиск противоречий, предложение правок, impact.

Каждый агент: промпт -> LLMClient.complete_json -> нормализованный результат.
При сбое LLM (LLMError) используется RuleBasedClient как фолбэк — сервис
работает даже без внешней модели.
"""
from __future__ import annotations

from app.services.llm import prompts as p
from app.services.llm.base import LLMClient, LLMError
from app.services.llm.rule_based import RuleBasedClient


class Agent:
    """Базовый класс: LLM + фолбэк."""

    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or RuleBasedClient()
        self.fallback = RuleBasedClient()

    def _ask(self, system: str, user: str) -> dict:
        try:
            return self.llm.complete_json(system, user)
        except LLMError:
            return self.fallback.complete_json(system, user)


class AuditAgent(Agent):
    """Аудит качества требования по DO-178C: атомарность, непротиворечивость, проверяемость."""

    def audit(self, text: str) -> dict:
        result = self._ask(p.AUDIT_SYSTEM, p.AUDIT_USER.format(text=text))
        issues = result.get("issues", []) if isinstance(result.get("issues"), list) else []
        score = int(result.get("score", 100))
        return {"score": max(0, min(100, score)), "issues": issues}

    def audit_requirement(self, text: str) -> dict:
        """Результат для хранения в analyses: одна запись на проблему."""
        res = self.audit(text)
        return {
            "score": res["score"],
            "issues": [{
                "type": i.get("type", "unknown"),
                "severity": i.get("severity", "warning"),
                "description": i.get("description", ""),
                "suggestion": i.get("suggestion", ""),
            } for i in res["issues"]],
        }


class ContradictionAgent(Agent):
    """Сравнение ДВУХ требований на логическое противоречие (пример из ТЗ).

    Используется RAG-сканером (rag.py) для пар кандидатов из разных разделов.
    """

    def compare(self, text_a: str, text_b: str, section_a: str = "?",
                section_b: str = "?") -> dict:
        result = self._ask(p.CONTRADICTION_SYSTEM,
                           p.CONTRADICTION_USER.format(text_a=text_a, text_b=text_b,
                                                       section_a=section_a, section_b=section_b))
        return {
            "contradiction": bool(result.get("contradiction", False)),
            "type": result.get("type", "none"),
            "explanation": result.get("explanation", ""),
            "suggestion": result.get("suggestion", ""),
        }


class EditAgent(Agent):
    """Предложение правки формулировки, устраняющей слабые места."""

    def propose(self, text: str, issues: list[dict] | None = None) -> dict:
        issues_txt = "\n".join(f"- [{i.get('type')}] {i.get('description')}" for i in (issues or []))
        result = self._ask(p.EDIT_SYSTEM, p.EDIT_USER.format(text=text, issues=issues_txt or "—"))
        if result.get("proposed_text"):
            return {"proposed_text": result["proposed_text"],
                    "rationale": result.get("rationale", "")}
        # фолбэк на эвристику, если LLM вернул пустоту
        return self.fallback.propose_edit(text, issues)


class ImpactAgent(Agent):
    """Impact-анализ: какие связанные требования затронет изменение."""

    def analyze(self, changed_text: str, related: list[dict]) -> dict:
        """related: [{'uid': ..., 'text': ...}] — дочерние/связанные требования."""
        # однострочный формат: uid | текст (текст без переносов, чтобы парсинг был надёжным)
        related_txt = "\n".join(f"{r['uid']} | {' '.join(r['text'].split())}" for r in related)
        result = self._ask(p.IMPACT_SYSTEM, p.IMPACT_USER.format(text=changed_text, related=related_txt))
        affected = result.get("affected", []) if isinstance(result.get("affected"), list) else []
        if affected and isinstance(affected[0], dict) and "uid" in affected[0]:
            return {"affected": [{"uid": a.get("uid", ""), "impact": a.get("impact", "")}
                                 for a in affected if isinstance(a, dict)]}
        return self.fallback.impact(changed_text, related_txt)
