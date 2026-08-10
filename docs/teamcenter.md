# Взаимодействие с Teamcenter 11 (SOA)

> **Для обслуживающего программиста.** Если синхронизация «не сходится» с вашей
> инсталляцией TC — откройте **`app/services/teamcenter/mapping.py`** и правьте
> ТОЛЬКО его: имена сервисов/операций и элементы ответов заданы там константами.
> Клиент и заглушка строятся на тех же константах, поэтому тесты сразу покажут,
> что формат изменился.

## Прототип

* Версия TC: 11.0
* URL: `http://org-tc2:8080/tc/services/`
* Ключевые связи: `IMAN_specification` (контент), `TC_Requirement_Trace_Relation` (трассируемость)
* **Рабочий протокол заказчика — REST** (`RestServices`): подтверждён боевым
  PHP-кодом (`getItemAndRelatedObjects`, `RequestEnvelope`, cookie
  `ASP.NET_SessionId`). Поддерживаются ОБА протокола, выбор — `TC_PROTOCOL`
  (`rest` по умолчанию | `soap`).

## REST-протокол (по умолчанию, проверен на проде)

Формат — ровно тот, что в рабочем PHP-клиенте:

```
POST http://org-tc2:8080/tc/services/RestServices/Core-2008-06-DataManagement/getItemAndRelatedObjects
Cookie: ASP.NET_SessionId=<сессия от login>

<?xml version="1.0" encoding="utf-8"?>
<RequestEnvelope xmlns="http://teamcenter.com/Schemas/Soa/2006-09/ClientContext">
  <header/>
  <body><bodystring><![CDATA[
    <GetItemAndRelatedObjectsInput xmlns="http://teamcenter.com/Schemas/Core/2008-06/DataManagement">
      <infos clientId="sync">
        <itemInfo clientId="sync" useIdFirst="1" uid="">
          <ids name="item_id" value="SPEC-BRAKE-001"/>
        </itemInfo>
        <revInfo clientId="sync" processing="All" useIdFirst="0" uid="" nRevs="2147483647" revisionRule=""/>
        <datasetInfo clientId="" uid=""/>
      </infos>
    </GetItemAndRelatedObjectsInput>
  ]]></bodystring></body>
</RequestEnvelope>
```

Ответ — `ResponseEnvelope` с ответом операции в CDATA `bodystring`:

```
<GetItemAndRelatedObjectsResponse ...>
  <item uid="item-SPEC-BRAKE-001">
    <item_id>SPEC-BRAKE-001</item_id>
    <object_name>...</object_name>
    <revision_list><item_revision uid="rev-SPEC-BRAKE-001-A">...</item_revision></revision_list>
  </item>
</GetItemAndRelatedObjectsResponse>
```

| Особенность | Как в коде |
|---|---|
| Вход в систему | `RestServices/Core-2007-01-Session/login`; сессия — cookie `ASP.NET_SessionId` из `Set-Cookie` |
| Готовая сессия | `TC_SESSION_ID` (например, полученная PHP-клиентом) |
| Нахождение спецификации | `getItemAndRelatedObjects` по `item_id` (проверенный формат) |
| Прочие операции | `getProperties`, `getRequirements`, `getChildren`, `findDatasets`, `getContents`, `getFileReadTicket`, `findRelations`, `setProperties` — те же имена, что в SOA |

**Если ваш REST-ответ отличается** — пути к элементам ответа заданы в
`mapping.py` константами `REST_*_PATH` (от корня ответа, без учёта namespace,
PascalCase), сервисы/операции — `REST_SVC_*`/`REST_OP_*`.

## SOAP-протокол (альтернатива: TC_PROTOCOL=soap)

## Используемые операции SOA

| Операция | Сервис | Назначение |
|---|---|---|
| `login` / `logout` | `Core-2007-01-Session` | Авторизация, выдаёт `AuthenticationToken` (SOAP-Header) |
| `findItems` | `Item-2006-06-Finder` | Поиск спецификации по id (`<criteria><name>..</name><type>..</type>`) |
| `getItemRevisions` | `Item-2006-06-Item` | Ревизии спецификации (корень дерева) |
| `getProperties` | `Core-2006-03-DataManagement` | Атрибуты любого объекта по uid (разделы) |
| `getRequirements` | `Requirement-2011-06-Requirement` | Атрибуты `RequirementRevision`, включая `object_string` (текст) |
| `getChildren` | `Structure-2007-01-Structure` | Дети узла: `child_uid`, `child_type`, `relation_name`, `sequence_no` |
| `findDatasets` | `Dataset-2006-06-Dataset` | Датасеты объекта по связи (`IMAN_specification`) |
| `getContents` | `Dataset-2006-06-Dataset` | Файлы датасета |
| `getFileReadTicket` | `FileManagement-2007-01-File` | URL для скачивания файла контента |
| `findRelations` | `Relation-2006-06-Relation` | Связи: `primary_object` (исходящие) / `secondary_object` (входящие) |
| `setProperties` | `Item-2006-06-Item` | **Запись** атрибутов (write-back правки в `object_string`) |

## Примеры запросов/ответов (как в прототипе)

### login
```xml
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:ser="http://www.teamcenter.com/soa/services/Core-2007-01-Session">
  <soapenv:Body>
    <ser:login><user>infodba</user><password>infodba</password>
      <group/><role/><discriminator/></ser:login>
  </soapenv:Body>
</soapenv:Envelope>
```
Ответ: токен в `<soapenv:Header><AuthenticationToken>…</AuthenticationToken></soapenv:Header>`,
все последующие вызовы несут его в Header.

### getChildren (обход дерева)
```xml
<ser:getChildren><input><child_uid>rev-SPEC-BRAKE-001-A</child_uid></input></ser:getChildren>
```
```xml
<getChildrenResponse>
  <output uid="rev-SS-1200-A" child_type="SpecSection">
    <child_uid>rev-SS-1200-A</child_uid>
    <relation_name>TC_Requirement_Spec_Reference</relation_name>
    <child_type>SpecSection</child_type>
    <sequence_no>2</sequence_no>
  </output>
  ...
</getChildrenResponse>
```

### getRequirements (RequirementRevision)
```xml
<ser:getRequirements>
  <requirement_revision>rev-REQ-1201-A</requirement_revision>
</ser:getRequirements>
```
```xml
<getRequirementsResponse>
  <requirement uid="rev-REQ-1201-A">
    <item_id>REQ-1201</item_id>
    <item_revision_id>A</item_revision_id>
    <object_name>Усилие на органе управления</object_name>
    <object_string>Максимальное усилие … не более 100 Н.</object_string>
    <type_name>RequirementRevision</type_name>
    <owning_user>petrov</owning_user>
    <last_modified>2026-08-10T12:00:00Z</last_modified>
  </requirement>
</getRequirementsResponse>
```

### Контент (IMAN_specification)
1. `findDatasets` → датасет (`HTMLDataset`/`TextDataset`) с `relation_name=IMAN_specification`;
2. `getContents` → файл (`REQ-1201.html`);
3. `getFileReadTicket` → URL вида `http://host/tc/files/{uid}`;
4. GET по URL → HTML; текст извлекается (теги/`head` отбрасываются,
   поддерживаются utf-8 и windows-1251).

Если датасета нет — текст берётся из `object_string` (фолбэк).

### setProperties (запись правки обратно в TC)
```xml
<ser:setProperties>
  <input>
    <object>rev-REQ-1201-A</object>
    <properties><object_string>Новый текст требования…</object_string></properties>
  </input>
</ser:setProperties>
```

## Как поправить формат под свою инсталляцию

1. **Имена операций/сервисов** — константы `SVC_*`/`OP_*` (SOAP) и
   `REST_SVC_*`/`REST_OP_*` (REST) в `mapping.py`.
2. **Пути к элементам ответа** — константы `*_PATH` (кортежи localname'ов;
   для SOAP — от SOAP Body, для REST — от корня ответа; namespace игнорируется).
3. **Имена атрибутов** — `ATTR_*`, `TYPE_*`, `REL_*`.
4. **Новые атрибуты в raw_data** — добавлять ничего не нужно: в БД уходит весь
   элемент ответа (`attrs` в `_parse_item`).

После правки запустите `python -m pytest tests/test_stub_soap.py tests/test_rest_client.py tests/test_sync.py`:
заглушка и клиенты используют одни константы, тесты покажут расхождения контракта.

## Устойчивость клиентов (обоих протоколов)

* таймауты раздельно: соединение (`TC_CONNECT_TIMEOUT`) и чтение (`TC_TIMEOUT`);
* ретраи сетевых ошибок и HTTP 5xx с backoff (`TC_RETRIES`); SOAP Fault/`<error>`
  — бизнес-ошибки, не ретраятся;
* автоперелогин: сессия истекла посреди синхронизации → клиент сам заходит
  заново и повторяет вызов один раз;
* пагинация `getChildren` (`TC_PAGE_SIZE` + `start_index`): спецификации на
  тысячи требований выкачиваются страницами;
* защита от SSRF: файлы контента скачиваются только с хоста TC
  (`TC_ALLOW_EXTERNAL_FILES=false`), лимит размера файла (`TC_MAX_CONTENT_BYTES`);
* проверка TLS-сертификата (`TC_VERIFY_SSL`).

## Заглушка Teamcenter (stub_tc)

Полноценный SOAP-сервер, имитирующий TC 11 (та же точка `/tc/services/`),
для разработки без доступа к реальной системе:

```bash
uvicorn stub_tc.server:app --port 9080
# в .env: TC_URL=http://127.0.0.1:9080/tc/services/
```

* Данные — `stub_tc/fixtures/brake_system.yaml` (спецификация тормозной системы
  самолёта с «посаженными» дефектами для демонстрации анализа).
* Состояние — в памяти; изменения через `setProperties` сохраняются.
* Отладка: `GET /admin` (HTML-список объектов), файлы контента — `GET /tc/files/{uid}`.
* Операции и их XML генерируются по тем же константам `mapping.py`, что и клиент, —
  контракт проверяется тестами «клиент ↔ заглушка» целиком.

## Тонкости

* Разбор ответов — по localname без namespace: реальный TC оборачивает элементы
  в свои namespace, нам они не важны.
* SOAP Fault'ы приходят с HTTP 500 — клиент разбирает тело и превращает их в
  `TcAuthError`/`TcSoapError` с внятным текстом.
* Синхронизация не удаляет строки: если требование исчезло из TC, оно остаётся
  в БД (последнее известное состояние) — это осознанно, для аудита.
