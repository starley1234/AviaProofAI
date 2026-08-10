# Архитектура AviaProofAI

## Назначение

Аналитический «цифровой двойник» требований над Teamcenter 11 (СУТ).
Система выкачивает требования из TC, анализирует их (LLM), даёт конструктору
дашборд со статусами, позволяет править требования в «двойнике» и — при
разрешённой записи — мигрировать готовую правку обратно в TC.

## Общая схема

```
┌─────────────┐   SOAP (SOA TC 11)   ┌─────────────────────┐
│ Teamcenter  │◄────────────────────►│  app/services/      │
│ 11 (или     │  login, getChildren, │  teamcenter/        │
│ stub_tc)    │  getRequirements,    │  mapping.py (формат)│
└──────┬──────┘  findDatasets,       │  client.py (SOAP)   │
       │        findRelations,       │  sync.py (ETL)      │
       │        setProperties        └──────────┬──────────┘
       │                                        │ SQLAlchemy
       │                              ┌─────────▼──────────┐
       │                              │ PostgreSQL         │
       │                              │ requirements,      │
       │                              │ *_snapshots,       │
       │                              │ *_drafts, analyses,│
       │                              │ sync_runs, users,  │
       │                              │ app_settings       │
       │                              └─────────┬──────────┘
       │                                        │
       │                              ┌─────────▼──────────┐      ┌──────────────┐
       │                              │ app/services/llm/  │      │ app/api/     │
       │                              │ agent.py, rag.py   │─────►│ FastAPI REST │
       │                              │ (OpenAI-совместимый│      │ /api/v1/*    │
       │                              │  или rule-based)   │      └──────┬───────┘
       │                              └────────────────────┘             │ HTTP+JSON
       │                                                       ┌─────────▼─────────┐
       │                                                       │ Koseven (PHP UI) │
       │                                                       │ рабочее место    │
       └──────────────── правки (только при разрешении) ──────►│ конструктора     │
                                                               └───────────────────┘
```

## Модули

| Модуль | Отвечает за |
|---|---|
| `app/services/teamcenter/` | Взаимодействие с TC 11: `mapping.py` — **единственное место** с форматом SOA (сервисы, операции, элементы); `client.py` — транспорт; `sync.py` — `TeamcenterSync` (ETL) и write-back правок |
| `app/models.py`, `app/db.py` | Схема PostgreSQL и сессии |
| `app/services/llm/` | Ядро: агенты (аудит, противоречия, правки, impact), RAG-сканер (TF-IDF), клиенты LLM (OpenAI-совместимый HTTP + эвристический фолбэк) |
| `app/services/analysis_service.py` | Пайплайн анализа и пересчёт статусов дашборда |
| `app/services/traceability.py` | Guardrail №1: требования без связей → «Риск сертификации» |
| `app/services/impact.py` | Guardrail №2: влияние изменения на связанные требования |
| `app/services/twin.py` | Жизненный цикл правки-двойника (draft → … → pushed) |
| `app/services/settings_service.py` | Права записи в TC (сервис + пользователь) |
| `app/api/` | REST API для Koseven (см. docs/api.md) |
| `stub_tc/` | Заглушка Teamcenter 11 (SOAP-сервер) для разработки и тестов |
| `koseven/` | Справочный PHP-модуль (интерфейс конструктора) |
| `tests/` | 60+ тестов: контракт SOAP, ETL, LLM, API, guardrails, права |

## Ключевые потоки

**Синхронизация** (`TeamcenterSync.run`): авторизация → поиск спецификации →
рекурсивный обход (разделы/требования) → для каждой RequirementRevision:
атрибуты (`getRequirements`), текст из HTML-датасета по связи
`IMAN_specification`, связи `TC_Requirement_Trace_Relation` → upsert в
`requirements` + снапшот в `requirement_snapshots` при изменении → журнал
`sync_runs`.

**Анализ** (`AnalysisService`): аудит качества (атомарность/непротиворечивость/
проверяемость по DO-178C) → RAG-скан противоречий между разделами →
traceability-аудит → пересчёт статусов (`ok | weak | conflict | needs_edit |
cert_risk`) → кэш статусов на строке требования для быстрого дашборда.

**Правка (двойник)** (`TwinService`): ИИ предлагает формулировку (или
конструктор вводит свою) → правка живёт в `requirement_drafts` с версиями →
«состояние готовности» (`ready`) → миграция в TC через `setProperties`
(object_string) → статус `pushed`, история остаётся.

**Права записи**: итог = `app_settings['teamcenter.write_allowed']` **И**
`users.write_to_tc_allowed`. Безопасный дефолт — запись запрещена
(см. docs/api.md, раздел «Настройки»).

## Почему правки не пишутся сразу в TC

TC — «источник правды» для сертификации. Двойник отделён: пока правка не
прошла подтверждение конструктора и не получила статус `ready`, она не покидает
PostgreSQL. Это требование ТЗ («до его фиксации в официальной системе») и
одновременно защита от порчи официальных данных ошибочным ИИ.

## Запуск

```bash
pip install -r requirements.txt
cp .env.example .env          # поправить DATABASE_URL, TC_URL, API_KEYS
python -m app.cli init-db
python -m app.cli serve       # uvicorn app.main:app --port 8080
# отдельно, для разработки без реального TC:
uvicorn stub_tc.server:app --port 9080
python -m app.cli sync
python -m app.cli audit && python -m app.cli conflicts && python -m app.cli traceability
```

Тесты (требуют только Python; PostgreSQL и «Teamcenter» поднимаются сами):

```bash
python -m pytest tests/ -q
```
