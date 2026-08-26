# LLM-ядро: агенты и RAG

## Состав

| Файл | Назначение |
|---|---|
| `base.py` | Интерфейс `LLMClient.complete_json(system, user) -> dict`, `parse_json_robust` |
| `http_client.py` | `HttpLLMClient` — OpenAI-совместимый endpoint (`/chat/completions`, `response_format=json_object`): OpenAI, vLLM, Ollama, LM Studio |
| `rule_based.py` | `RuleBasedClient` — детерминированные эвристики без сети (демо, тесты, фолбэк) |
| `prompts.py` | Промпты агентов; схемы JSON совпадают с результатами эвристик — ИИ и фолбэк взаимозаменяемы |
| `agent.py` | `AuditAgent`, `ContradictionAgent`, `EditAgent`, `ImpactAgent`; при `LLMError` — автоматический фолбэк на эвристики |
| `rag.py` | `TfidfIndex` + `ConflictScanner`: поиск пар-кандидатов из разных разделов, передача в `ContradictionAgent` |
| `factory.py` | `build_llm(settings)`: ключ задан → HTTP-клиент, иначе rule-based |

## Аудит качества (DO-178C)

`AuditAgent.audit(text)` проверяет:
* **атомарность** — одно требование = одно утверждение (один глагол-требование; «и/или» — признак);
* **непротиворечивость** — нет конфликтующих ограничений внутри требования;
* **проверяемость** — нет «достаточно…», «при необходимости», «нормальные условия» и т.п.

Результат: `{"score": 0..100, "issues": [{"type", "severity", "description", "suggestion"}]}`.

## Поиск противоречий (пример агента из ТЗ)

`ContradictionAgent.compare(a, b)` — сравнение ДВУХ требований:

```
Промпт: «…противоречие — когда ОБА требования не могут выполняться одновременно…»
Результат: {"contradiction": bool, "type": "none|numeric_range|polarity|incompatible_constraint",
            "explanation": "…", "suggestion": "…"}
```

Эвристики (фолбэк):
* числовые диапазоны: «не более 100 Н» vs «не менее 150 Н» при общей единице → `numeric_range`;
* полярность: «должен X» vs «не должен X» → `polarity`;
* непохожие тексты (Jaccard < 0.3) → не противоречат.

## RAG-сканер несостыковок

1. Все требования индексируются (TF-IDF, чистый Python);
2. для каждого требования — top-k ближайших **из других разделов**;
3. пары-кандидаты проверяются `ContradictionAgent`'ом;
4. найденные противоречия — в `analyses` (kind=`conflict`, severity=`critical`).

Подключение настоящих эмбеддингов: реализуйте `embed(text)` и замените
`TfidfIndex.vectors` на матрицу эмбеддингов — интерфейс сканера не меняется.

## Предложение правок

`EditAgent.propose(text, issues)` → `{"proposed_text", "rationale"}`: сохраняет
смысл, заменяет непроверяемые слова измеримыми критериями (например,
«достаточно надёжной» → «со средней наработкой на отказ не менее 10 000 ч»),
разбивает неатомарные требования.

## Impact-анализ

`ImpactAgent.analyze(new_text, related)` → `{"affected": [{"uid", "impact"}]}` —
какие связанные (дочерние) требования затронет изменение.
