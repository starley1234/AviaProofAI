# REST API: Python (аналитика) ↔ Koseven (интерфейс)

Базовый URL: `http://<host>:8080/api/v1` · OpenAPI-документация: `/docs`
(при запущенном сервисе).

## Аутентификация и пользователь

| Заголовок | Обязателен | Назначение |
|---|---|---|
| `X-Api-Key` | да | ключ сервиса (переменная `API_KEYS`), иначе `401` |
| `X-TC-User` | для действий пользователя | tc_login из маппинга `users`; по нему проверяется право записи в TC (`403`, если пользователя нет) |

Koseven присылает `X-TC-User`, полученный из маппинга `koseven_login → tc_login`
(эндпоинт `GET /users`).

## Эндпоинты

### Синхронизация
| Метод/путь | Назначение |
|---|---|
| `POST /sync/run` `{"spec_id": "…", "wait": bool}` | Запуск синхронизации. `wait=true` — синхронно (ответ со статистикой); иначе фоновая задача, `{"run_id": N}` |
| `GET /sync/runs?limit=20` | Журнал прогонов |
| `GET /sync/runs/{id}` | Статус прогона (`running/ok/failed`, `stats: {created, updated, unchanged, errors}`) |

### Дашборд и требования
| Метод/путь | Назначение |
|---|---|
| `GET /dashboard/summary` | Сводка: `by_status`, `total`, `write_access`, `last_sync` |
| `GET /requirements?status=&search=&section=&limit=` | Список (статусы: ok/weak/conflict/needs_edit/cert_risk) |
| `GET /requirements/{uid}` | Карточка: текст из TC, `raw_data`, связи, замечания, история правок |
| `GET /requirements/{uid}/traceability` | Связи вверх/вниз + дети по дереву |
| `POST /requirements/{uid}/impact` `{"new_text": "…"}` | Impact-анализ: какие связанные требования затронет изменение |

### Анализ (LLM + guardrails)
| Метод/путь | Назначение |
|---|---|
| `POST /analysis/audit` | Аудит качества всех требований (атомарность/непротиворечивость/проверяемость) |
| `POST /analysis/conflicts` | RAG-скан противоречий между разделами |
| `POST /analysis/traceability` | Traceability-аудит → «Риск сертификации» для несвязанных |
| `GET /analysis/results?kind=&status=&requirement_uid=` | Замечания |
| `POST /analysis/results/{id}/resolve` `{"resolution": "resolved\|dismissed\|acknowledged"}` | Закрыть/отклонить замечание |

### Двойник (правки)
| Метод/путь | Назначение |
|---|---|
| `POST /requirements/{uid}/drafts` `{"source": "ai"\|"user", "text"?, "rationale"?}` | Создать правку: `ai` — ИИ предложит формулировку, `user` — вручную |
| `GET /drafts/{id}` | Правка |
| `PATCH /drafts/{id}` `{"text"?, "rationale"?}` | Редактирование рабочего варианта |
| `POST /drafts/{id}/submit` | **«Состояние готовности»** (`ready`) |
| `POST /drafts/{id}/reject` | Отклонить |
| `POST /drafts/{id}/push` | **Миграция в Teamcenter** (`setProperties` object_string). `403` — запись запрещена; `409` — правка не в статусе `ready` |

### Настройки записи в Teamcenter и пользователи
| Метод/путь | Назначение |
|---|---|
| `GET /settings/write-access?user=` | `{service_wide_allowed, per_user_allowed, effective}` — итог = И |
| `PUT /settings/write-access` `{"enabled": bool}` | Глобальный выключатель записи (только `role=admin`) |
| `GET /users` | Маппинг TC↔Koseven и права |
| `POST /users` `{"tc_login","koseven_login","write_to_tc_allowed",…}` | Создать пользователя |
| `PATCH /users/{id}` | Изменить роль/право записи/активность |

### Служебное
`GET /health` (без ключа) · `GET /api/v1/ping` (с ключом).

## Коды ошибок

| Код | Смысл |
|---|---|
| `401` | неверный/отсутствующий `X-Api-Key` |
| `403` | пользователь не в маппинге; нет роли admin; **запись в TC запрещена настройками** |
| `404` | требование/правка/замечание не найдены |
| `409` | неверное состояние (правка не `ready`, дубликат пользователя) |
| `422` | невалидные параметры |

## Примеры

### ИИ предлагает правку, конструктор отправляет в TC
```
POST /api/v1/requirements/rev-REQ-1101-A/drafts
X-Api-Key: dev-key-change-me   X-TC-User: petrov
{"source": "ai"}
→ {"id": 1, "status": "proposed", "proposed_text": "…со средней наработкой на отказ не менее 10 000 ч…",
   "current_text": "…", "issues": […], "version": 1}

PATCH /api/v1/drafts/1            {"text": "…уточнённый текст…", "rationale": "…"}
POST   /api/v1/drafts/1/submit    → {"status": "ready"}
POST   /api/v1/drafts/1/push      → {"status": "pushed", "pushed_at": "…"}   # или 403
```

### Дашборд
```
GET /api/v1/dashboard/summary
→ {"total": 16, "by_status": {"ok": 10, "weak": 2, "conflict": 1, "cert_risk": 3},
   "write_access": {"service_wide_allowed": false, "per_user_allowed": true, "effective": false},
   "last_sync": {"status": "ok", "stats": {"created": 16, "updated": 0, "unchanged": 0}}}
```

## Контракт для Koseven

PHP-клиент со всеми этими вызовами — `koseven/application/classes/Avia/Api.php`
(каждый метод = один эндпоинт). Никакой логики в PHP нет: сервис сам решает,
разрешать ли запись, что подсветить и какую правку предложить.
