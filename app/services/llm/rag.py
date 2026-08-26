"""RAG-сканер несостыковок: поиск кандидатов на противоречие.

Векторный поиск заменён лёгким TF-IDF (чистый Python, без зависимостей):
требования индексируются, для каждого ищутся ближайшие соседи ИЗ ДРУГИХ
РАЗДЕЛОВ спецификации; пары-кандидаты передаются ContradictionAgent'у.

Чтобы подключить настоящие эмбеддинги: реализуйте embedder (например,
через LLMClient) с методом embed(text) -> list[float] и замените
TfidfIndex.vectors на матрицу эмбеддингов — интерфейс сканера не изменится.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from app.services.llm.agent import ContradictionAgent


def _tokens(text: str) -> list[str]:
    return re.findall(r"[а-яёa-z]{3,}", text.lower())


class TfidfIndex:
    """Минимальный TF-IDF индекс: документы -> векторы, косинусная близость."""

    def __init__(self):
        self.docs: list[str] = []
        self._df: Counter = Counter()
        self._tf: list[Counter] = []
        self.vectors: list[dict[str, float]] = []

    def add(self, text: str) -> None:
        toks = _tokens(text)
        tf = Counter(toks)
        self.docs.append(text)
        self._tf.append(tf)
        for t in set(toks):
            self._df[t] += 1

    def build(self) -> None:
        n = len(self._tf)
        self.vectors = []
        for tf in self._tf:
            vec: dict[str, float] = {}
            for t, c in tf.items():
                idf = math.log((1 + n) / (1 + self._df[t])) + 1
                vec[t] = c * idf
            self.vectors.append(vec)

    @staticmethod
    def _cos(a: dict[str, float], b: dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        dot = sum(a[t] * b[t] for t in a if t in b)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    def nearest(self, i: int, top_k: int, exclude: set[int] | None = None) -> list[tuple[int, float]]:
        exclude = exclude or set()
        scores = [(j, self._cos(self.vectors[i], self.vectors[j]))
                  for j in range(len(self.vectors)) if j != i and j not in exclude]
        scores.sort(key=lambda x: -x[1])
        return scores[:top_k]


class ConflictScanner:
    """Сканирует спецификацию и возвращает найденные противоречия.

    >>> scanner = ConflictScanner(agent, top_k=5, min_similarity=0.15)
    >>> scanner.scan(requirements)  # requirements: [{'uid','text','section_path'}]
    [{'uid_a','uid_b','type','explanation','suggestion'}, ...]
    """

    def __init__(self, agent: ContradictionAgent, top_k: int = 5, min_similarity: float = 0.15):
        self.agent = agent
        self.top_k = top_k
        self.min_similarity = min_similarity

    def scan(self, requirements: list[dict]) -> list[dict]:
        if len(requirements) < 2:
            return []
        index = TfidfIndex()
        for r in requirements:
            index.add(r["text"])
        index.build()

        seen: set[tuple[str, str]] = set()
        conflicts: list[dict] = []
        for i, a in enumerate(requirements):
            for j, sim in index.nearest(i, self.top_k):
                b = requirements[j]
                if a["uid"] == b["uid"] or a["section_path"] == b["section_path"]:
                    continue  # только разные разделы
                if sim < self.min_similarity:
                    continue
                pair = tuple(sorted((a["uid"], b["uid"])))
                if pair in seen:
                    continue
                seen.add(pair)
                res = self.agent.compare(a["text"], b["text"],
                                         section_a=a["section_path"], section_b=b["section_path"])
                if res["contradiction"]:
                    conflicts.append({
                        "uid_a": a["uid"], "uid_b": b["uid"],
                        "section_a": a["section_path"], "section_b": b["section_path"],
                        "type": res["type"], "explanation": res["explanation"],
                        "suggestion": res["suggestion"],
                    })
        return conflicts
