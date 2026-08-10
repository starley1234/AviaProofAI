# AviaProofAI — аналитический цифровой двойник требований

Интеллектуальный слой над **Teamcenter 11 (СУТ)**: выкачивает требования,
анализирует их (LLM по DO-178C), показывает конструктору слабые места и
несостыковки и возвращает подтверждённые правки обратно в TC.

```
ИИ находит проблему → конструктор подтверждает/правит в «двойнике» →
правка получает «состояние готовности» → миграция обратно в СУТ TC
```

## Возможности

* **ETL (Python):** `TeamcenterSync` — SOA-авторизация, рекурсивный обход
  спецификации, выгрузка `RequirementRevision`, извлечение текста из
  HTML/Text-датасетов по связи `IMAN_specification`, связи
  `TC_Requirement_Trace_Relation`; PostgreSQL + `jsonb` (`raw_data`,
  `traceability_links`), версионность состояний (снапшоты).
* **LLM-ядро:** аудит качества (атомарность/непротиворечивость/проверяемость,
  DO-178C), RAG-поиск противоречий между разделами, предложение формулировок
  правок, impact-анализ. OpenAI-совместимый endpoint или встроенные эвристики
  (работает без сети).
* **REST API** для Koseven (PHP): дашборд со статусами
  «Ок / Слабое место / Конфликт связей / Требует правки / Риск сертификации»,
  карточка требования, жизненный цикл правки-двойника, настройки.
* **Guardrails:** Traceability Audit (требование без связей → «Риск
  сертификации»), Impact Analysis (изменение подсвечивает связанные требования).
* **Права записи в TC:** глобальный выключатель сервиса **И** право
  пользователя; безопасный дефолт — запись запрещена.
* **Заглушка Teamcenter** (`stub_tc`) — правдоподобный SOAP-сервер TC 11 для
  разработки и тестов, с фикстурой авиационной спецификации.

## Быстрый старт (демо на заглушке)

```bash
pip install -r requirements.txt
cp .env.example .env
# в .env: DATABASE_URL — ваш PostgreSQL; TC_URL=http://127.0.0.1:9080/tc/services/

# 1) заглушка Teamcenter (имитация TC 11)
uvicorn stub_tc.server:app --port 9080 &

# 2) сервис
python -m app.cli init-db
python -m app.cli serve &          # http://127.0.0.1:8080/docs

# 3) синхронизация и анализ
python -m app.cli sync --spec SPEC-BRAKE-001
python -m app.cli audit
python -m app.cli conflicts        # найдёт противоречие REQ-1201 ↔ REQ-1301 (100 Н vs 150 Н)
python -m app.cli traceability     # «Риск сертификации» для несвязанных требований
python -m app.cli dashboard
```

Или одной командой: `bash scripts/demo.sh`.

## Тесты (60+)

Поднимают настоящий PostgreSQL (embedded) и заглушку TC автоматически:

```bash
python -m pytest tests/ -q
```

## Структура репозитория

```
app/                     Python-сервис (FastAPI)
  services/teamcenter/   mapping.py — ЕДИНСТВЕННОЕ место с форматом SOA TC
                         client.py — SOAP-клиент; sync.py — TeamcenterSync (ETL)
  services/llm/          агенты (аудит, противоречия, правки, impact), RAG, клиенты LLM
  services/              twin.py (двойник), analysis, traceability, impact, settings
  models.py, db.py       схема PostgreSQL
  api/                   REST API /api/v1
stub_tc/                 заглушка Teamcenter 11 (SOAP) + фикстура спецификации
koseven/                 справочный PHP-модуль (интерфейс конструктора)
docs/                    архитектура, Teamcenter, БД, API, LLM
tests/                   контракт SOAP, ETL, LLM, API, guardrails, права записи
```

## Документация

* [docs/architecture.md](docs/architecture.md) — общая схема и потоки
* [docs/teamcenter.md](docs/teamcenter.md) — **взаимодействие с TC 11: формат,
  операции, как править под свою инсталляцию**
* [docs/database.md](docs/database.md) — схема БД и модель версионности
* [docs/api.md](docs/api.md) — контракт REST API для Koseven
* [docs/llm.md](docs/llm.md) — LLM-ядро и RAG
* [koseven/README.md](koseven/README.md) — установка PHP-модуля

## Запись в Teamcenter (важно)

По умолчанию **запрещена**. Чтобы разрешить:

```bash
# 1) право пользователя (в БД сервиса)
python -m app.cli users add petrov --write
# 2) глобальный выключатель
python -m app.cli write-on
```

Итоговое правило: `app_settings['teamcenter.write_allowed']` И
`users.write_to_tc_allowed` (см. `GET /api/v1/settings/write-access`).

## Требования

Python 3.11+, PostgreSQL 13+ (jsonb), Koseven/Kohana 3.3+ для UI-модуля.
