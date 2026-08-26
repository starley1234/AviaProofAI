"""Тесты LLM-ядра: аудит качества, противоречия, правки, RAG-сканер.

Используется RuleBasedClient (без сети) — детерминированные ожидания.
"""
from __future__ import annotations

from app.services.llm.agent import AuditAgent, ContradictionAgent, EditAgent, ImpactAgent
from app.services.llm.rag import ConflictScanner
from app.services.llm.rule_based import RuleBasedClient


def test_audit_finds_verifiability_issue():
    res = AuditAgent(RuleBasedClient()).audit_requirement(
        "Система должна быть достаточно надёжной в эксплуатации.")
    types = {i["type"] for i in res["issues"]}
    assert "verifiability" in types
    assert res["score"] < 100


def test_audit_finds_atomicity_issue():
    res = AuditAgent(RuleBasedClient()).audit_requirement(
        "Система должна иметь два канала; система должна контролировать давление.")
    types = {i["type"] for i in res["issues"]}
    assert "atomicity" in types


def test_audit_clean_requirement():
    res = AuditAgent(RuleBasedClient()).audit_requirement(
        "Максимальное усилие на педали должно быть не более 100 Н.")
    assert res["issues"] == [] and res["score"] == 100


def test_contradiction_numeric_range():
    """Пример из ТЗ: сравнение двух требований на логическое противоречие."""
    a = "Максимальное усилие на органе управления торможением должно быть не более 100 Н."
    b = "Минимальное усилие на органе управления торможением должно быть не менее 150 Н."
    res = ContradictionAgent(RuleBasedClient()).compare(a, b, "2", "3")
    assert res["contradiction"] is True
    assert res["type"] == "numeric_range"
    assert "100" in res["explanation"] and "150" in res["explanation"]
    assert res["suggestion"]


def test_contradiction_polarity():
    a = "Система должна автоматически растормаживать колёса при убранной педали."
    b = "Система не должна автоматически растормаживать колёса при убранной педали."
    res = ContradictionAgent(RuleBasedClient()).compare(a, b)
    assert res["contradiction"] is True and res["type"] == "polarity"


def test_no_contradiction_for_unrelated():
    a = "Тормозной путь не должен превышать 1500 м."
    b = "Система должна выдавать сигнализацию об отказе канала."
    res = ContradictionAgent(RuleBasedClient()).compare(a, b)
    assert res["contradiction"] is False


def test_edit_proposal_replaces_vague_wording():
    res = EditAgent(RuleBasedClient()).propose(
        "Система торможения должна быть достаточно надёжной в эксплуатации.")
    assert "10 000" in res["proposed_text"]  # измеримый критерий вместо «достаточно надёжной»
    assert res["rationale"]


def test_edit_proposal_splits_atomic():
    res = EditAgent(RuleBasedClient()).propose(
        "Система должна иметь два канала; система должна контролировать давление.")
    assert res["proposed_text"].count("1.") == 1 and "2." in res["proposed_text"]


def test_impact_analysis():
    agent = ImpactAgent(RuleBasedClient())
    res = agent.analyze("Система торможения должна обеспечивать торможение при разбеге.",
                        [{"uid": "rev-REQ-1201-A", "text": "Усилие на педали не более 100 Н."},
                         {"uid": "rev-REQ-9999-A", "text": "Система должна обеспечивать торможение при разбеге и пробеге."}])
    # как и в ImpactService: «не затронуто» отфильтровывается
    affected = {a["uid"] for a in res["affected"] if "не затрон" not in a.get("impact", "")}
    assert "rev-REQ-9999-A" in affected
    assert "rev-REQ-1201-A" not in affected


def test_conflict_scanner_finds_planted_pair():
    reqs = [
        {"uid": "A", "text": "Максимальное усилие на органе управления торможением, "
                             "необходимое для создания полного тормозного давления, должно быть не более 100 Н.",
         "section_path": "2"},
        {"uid": "B", "text": "Минимальное усилие на органе управления торможением, "
                             "необходимое для создания полного тормозного давления, должно быть не менее 150 Н.",
         "section_path": "3"},
        {"uid": "C", "text": "Система должна выдавать сигнализацию об отказе каждого канала управления.",
         "section_path": "4"},
        {"uid": "D", "text": "Средний ресурс тормозных дисков должен составлять не менее 500 посадок.",
         "section_path": "2"},
    ]
    scanner = ConflictScanner(ContradictionAgent(RuleBasedClient()), top_k=3, min_similarity=0.1)
    conflicts = scanner.scan(reqs)
    assert len(conflicts) == 1
    assert {conflicts[0]["uid_a"], conflicts[0]["uid_b"]} == {"A", "B"}


def test_tfidf_index_ranking():
    from app.services.llm.rag import TfidfIndex
    idx = TfidfIndex()
    for t in ["тормозная система", "система управления тормозом", "сигнализация отказов"]:
        idx.add(t)
    idx.build()
    n = idx.nearest(0, 2)
    assert n[0][0] == 1 and n[0][1] > 0  # общее слово «система» -> ближе, чем к «сигнализации»
    assert n[1][0] == 2 and n[1][1] == 0.0


def test_parse_json_robust():
    from app.services.llm.base import parse_json_robust
    assert parse_json_robust('{"a": 1}') == {"a": 1}
    assert parse_json_robust('Вот ответ: {"a": 1} и всё.') == {"a": 1}
    assert parse_json_robust("не json") is None
