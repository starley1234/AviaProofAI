# Koseven-модуль «AviaProofAI» (рабочее место конструктора)

Тонкий PHP-клиент поверх REST API аналитического сервиса (Python).
Вся логика (синхронизация, LLM-анализ, guardrails) — в сервисе;
здесь только интерфейс: дашборд, карточка требования, правки, настройки.

## Установка

1. Скопируйте файлы в приложение Koseven (Kohana 3.3+):

   ```
   application/config/avia.php
   application/classes/Avia/Api.php
   application/classes/Controller/Avia.php
   application/views/avia/dashboard.php
   application/views/avia/requirement.php
   application/views/avia/drafts.php
   ```

2. Настройте `application/config/avia.php`:

   ```php
   return array(
       'api_url'    => 'http://127.0.0.1:8080/api/v1', // адрес Python-сервиса
       'api_key'    => 'dev-key-change-me',            // тот же, что в .env сервиса
       'llm_model'  => 'rule-based',                   // информационно
   );
   ```

3. Маршрут (bootstrap.php):

   ```php
   Route::set('avia', 'avia(/<action>(/<id>))')
       ->defaults(array('controller' => 'avia', 'action' => 'index'));
   ```

4. Пользователи: маппинг `koseven_login -> tc_login` живёт в БД сервиса
   (таблица `users`, эндпоинт `GET /api/v1/users`). Админ заводит конструкторов
   через `POST /api/v1/users` или CLI сервиса (`python -m app.cli users add petrov`).

## Что делает модуль

| Экран | Action | Вызовы API |
|---|---|---|
| Дашборд со статусами | `index` | `GET /dashboard/summary`, `GET /requirements` |
| Карточка требования | `requirement` | `GET /requirements/{uid}`, `POST /requirements/{uid}/drafts` |
| Правка (двойник) | `draft` | `PATCH /drafts/{id}`, `POST /drafts/{id}/submit`, `POST /drafts/{id}/push` |
| Разрешение замечаний | `resolve` | `POST /analysis/results/{id}/resolve` |
| Настройки записи в TC | `settings` | `GET|PUT /settings/write-access` (только admin) |

Каждый запрос к API несёт заголовки:

```
X-Api-Key:  <ключ сервиса>
X-TC-User:  <tc_login из маппинга>   — по нему сервис проверяет право записи в Teamcenter
```

## Безопасность по умолчанию

Даже если у конструктора открыт экран «Отправить в Teamcenter», сервис
отклонит запись (HTTP 403), пока администратор не включит:
- глобальный выключатель `PUT /api/v1/settings/write-access {"enabled": true}`,
- право пользователя `write_to_tc_allowed=true` в таблице `users`.
