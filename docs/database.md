# Структура базы данных (PostgreSQL)

Два слоя данных (требование ТЗ): **слой Teamcenter** (исходная правда,
перезаписывается синхронизацией) и **аналитический слой** (двойник: версии
правок ИИ/конструктора до фиксации в TC). Модели — `app/models.py`.

```
┌─ Слой Teamcenter ─────────────────────────────────────────────┐
│ requirements           текущее состояние из TC (upsert)        │
│ requirement_snapshots  неизменяемая история состояний          │
│ sync_runs              журнал синхронизаций                    │
├─ Аналитический слой ──────────────────────────────────────────┤
│ requirement_drafts     версии правок: ИИ/конструктор,          │
│                        статус готовности до миграции в TC      │
│ analyses               замечания: аудит/конфликт/impact/       │
│                        traceability                            │
├─ Сервис ───────────────────────────────────────────────────────┤
│ users                  маппинг TC <-> Koseven + право записи   │
│ app_settings           глобальные настройки (запись в TC)      │
└────────────────────────────────────────────────────────────────┘
```

## requirements — текущее состояние требования

| Колонка | Тип | Описание |
|---|---|---|
| `uid` | PK varchar | uid объекта из TC (ревизии) |
| `item_id`, `item_revision_id` | varchar | REQ-1201 / A |
| `type` | varchar | `Specification` \| `SpecSection` \| `RequirementRevision` |
| `text` | text | извлечённый текст (HTML-датасет `IMAN_specification` или `object_string`) |
| `text_hash` | varchar | sha256 текста — детекция изменений при синхронизации |
| `parent_uid` | FK→requirements | родитель по дереву (раздел/спецификация) — вложенность требований |
| `section_path` | varchar | путь в спецификации: «2.1» |
| `spec_uid` | varchar | корень спецификации |
| `raw_data` | **jsonb** | ВСЕ атрибуты из TC (снимок элемента SOAP-ответа) |
| `traceability_links` | **jsonb** | `{"out": [uid…], "in": [uid…]}` по `TC_Requirement_Trace_Relation` |
| `status` | varchar | `ok` \| `weak` \| `conflict` \| `needs_edit` \| `cert_risk` — кэш для дашборда |
| `status_reasons` | jsonb | человекочитаемые причины статуса |
| `first_seen_at`, `last_synced_at` | timestamptz | |

## requirement_snapshots — история «как было»

Пишется при создании строки и при каждом изменении `text_hash`. Позволяет
ответить на вопрос «что было в TC до правки/до синхронизации N».

## requirement_drafts — аналитический слой (двойник)

| Колонка | Описание |
|---|---|
| `version` | 1, 2, 3… — история правок по требованию |
| `source` | `ai` (предложение ИИ) \| `user` (конструктор) |
| `original_text` | текст из TC на момент создания правки |
| `proposed_text` | формулировка, предложенная ИИ |
| `current_text` | **рабочий вариант** конструктора (то, что уйдёт в TC) |
| `rationale` | обоснование правки |
| `issues` | jsonb — какие замечания анализа правка закрывает |
| `status` | `draft → proposed → reviewed → ready → pushed \| rejected` |
| `created_by`, `pushed_at` | кто и когда |

**Состояние готовности** (`ready`) — «зелёный свет» перед миграцией в TC;
push возможен только из него (проверяется и в сервисе, и в API).

## analyses — замечания анализа

`kind`: `quality_audit` (аудит DO-178C), `conflict` (пара противоречий),
`impact` (влияние изменения), `traceability` (риск сертификации).
`severity`: info/warning/critical. `status`: open/acknowledged/resolved/dismissed.
`details` — jsonb (объяснение, предложение, доп. поля).

## users — маппинг пользователей

`tc_login` ↔ `koseven_login`, `role` (engineer/admin), `write_to_tc_allowed` —
право ЗАПИСИ в TC (per-user выключатель).

## app_settings — настройки сервиса

Ключ `teamcenter.write_allowed` (`{"enabled": bool}`) — глобальный выключатель
записи в TC. Итоговое право: **глобальный И пользовательский** флаги
(см. `app/services/settings_service.py`).

## Почему jsonb

`raw_data` и `traceability_links` — произвольные структуры, формат которых
диктует Teamcenter; jsonb позволяет хранить всё без миграций схемы при
изменении набора атрибутов TC и индексировать ключевые поля при необходимости.
В тестах используется настоящий PostgreSQL (embedded), поэтому jsonb
проверяется «по-настоящему».

## Миграции

На этапе прототипа схема создаётся автоматически (`init_db`, `python -m app.cli
init-db`). При развитии — подключите Alembic: модели уже готовы.
