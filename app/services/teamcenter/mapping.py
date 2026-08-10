"""Teamcenter 11 (SOA) — ОПИСАНИЕ ФОРМАТА ОБМЕНА. ЕДИНСТВЕННОЕ МЕСТО ПРАВКИ.

Всё, что касается «как именно Teamcenter отвечает», собрано здесь:
имена SOA-сервисов, операций, элементов запросов/ответов.

Если реальный TC 11 в вашей инсталляции отдаёт что-то в другом формате —
правьте ТОЛЬКО этот файл: и синхронизация (app/services/teamcenter/*),
и заглушка (stub_tc/*) строятся на этих константах, поэтому тесты сразу
покажут, что формат поменялся.

Проверено на прототипе: TC 11.0, URL http://org-tc2:8080/tc/services/
"""
from __future__ import annotations

# ═══════════════════════════ Транспорт SOAP ═══════════════════════════
SOAP_ENV_NS = "http://schemas.xmlsoap.org/soap/envelope/"
# Токен авторизации кладётся в SOAP-Header (см. soap.py -> _envelope)
AUTH_TOKEN_NS = "http://www.teamcenter.com/soa/common/AuthenticationToken"
AUTH_TOKEN_TAG = "AuthenticationToken"

# ═══════════════════════════ Имена SOA-сервисов ═══════════════════════════
SVC_SESSION = "Core-2007-01-Session"          # вход/выход
SVC_DATA_MGMT = "Core-2006-03-DataManagement" # getProperties — атрибуты любого объекта
SVC_ITEM_FINDER = "Item-2006-06-Finder"       # поиск объектов (спецификаций)
SVC_ITEM = "Item-2006-06-Item"                # атрибуты Item/ItemRevision
SVC_STRUCTURE = "Structure-2007-01-Structure"  # обход дерева спецификации
SVC_REQUIREMENT = "Requirement-2011-06-Requirement"  # сервис RMS (зарезервирован)
SVC_DATASET = "Dataset-2006-06-Dataset"       # датасеты (контент IMAN_specification)
SVC_FILE = "FileManagement-2007-01-File"      # скачивание файлов датасетов
SVC_RELATION = "Relation-2006-06-Relation"    # связи (TC_Requirement_Trace_Relation)

# ═══════════════════════════ Операции и их элементы ═══════════════════════════
# Формат: OP = ("имя_операции", {"имя_параметра": ...}) — параметры см. в client.py

# --- Session/login: вход в TC ---
OP_LOGIN = "login"
# Параметры запроса (в порядке следования в SOAP-теле):
LOGIN_PARAMS = ("user", "password", "group", "role", "discriminator")

# --- Session/logout ---
OP_LOGOUT = "logout"

# --- ItemFinder/findItems: поиск объекта по имени ---
OP_FIND_ITEMS = "findItems"
# Запрос: <findItems><criteria><name>..</name><type>..</type></criteria></findItems>
# Ответ:  <findItemsResponse><found><item>...</item></found></findItemsResponse>
# Путь к списку найденных Item (по localname, без учёта namespace):
FIND_ITEMS_PATH = ("findItemsResponse", "found", "item")

# --- Item/getItemRevisions: атрибуты ревизии ---
OP_GET_ITEM_REVISIONS = "getItemRevisions"
# Запрос: <getItemRevisions><input><item>UID_ITEM</item></input></getItemRevisions>
# Ответ:  <getItemRevisionsResponse><item_revision>...</item_revision></getItemRevisionsResponse>
REVISIONS_PATH = ("getItemRevisionsResponse", "item_revision")

# --- DataManagement/getProperties: атрибуты ЛЮБОГО объекта по uid (разделы, спецификация) ---
OP_GET_PROPERTIES = "getProperties"
# Запрос: <getProperties><input><object>UID</object><attributes><name>attr</name>...</attributes></input></getProperties>
#         (attributes опционален: без него возвращаются все атрибуты)
# Ответ:  <getPropertiesResponse><output><object uid=..>...атрибуты...</object></output></getPropertiesResponse>
GET_PROPERTIES_PATH = ("getPropertiesResponse", "output", "object")

# --- Requirement/getRequirements: атрибуты RequirementRevision (RMS) ---
OP_GET_REQUIREMENTS = "getRequirements"
# Запрос: <getRequirements><requirement_revision>UID</requirement_revision>...</getRequirements>
#         (элемент повторяется — по одному на каждую ревизию)
# Ответ:  <getRequirementsResponse><requirement>...</requirement></getRequirementsResponse>
REQUIREMENTS_PATH = ("getRequirementsResponse", "requirement")

# --- Structure/getChildren: дети узла (разделы/требования) ---
OP_GET_CHILDREN = "getChildren"
# Запрос: <getChildren><input><child_uid>UID</child_uid></input></getChildren>
# Ответ:  <getChildrenResponse><output>...</output></getChildrenResponse>
CHILDREN_PATH = ("getChildrenResponse", "output")

# --- Dataset/findDatasets: датасеты объекта (контент через IMAN_specification) ---
OP_FIND_DATASETS = "findDatasets"
# Запрос: <findDatasets><input><object>UID</object><relation_name>IMAN_specification</relation_name></input></findDatasets>
# Ответ:  <findDatasetsResponse><output><dataset>...</dataset></output></findDatasetsResponse>
DATASETS_PATH = ("findDatasetsResponse", "output", "dataset")

# --- Dataset/getContents: файлы датасета ---
OP_GET_CONTENTS = "getContents"
# Запрос: <getContents><input><dataset>UID</dataset></input></getContents>
CONTENTS_PATH = ("getContentsResponse", "output")

# --- File/getFileReadTicket: URL для скачивания файла ---
OP_GET_FILE_TICKET = "getFileReadTicket"
# Запрос: <getFileReadTicket><file>UID</file><target>..</target></getFileReadTicket>
TICKET_PATH = ("getFileReadTicketResponse", "ticket")

# --- Relation/findRelations: связи объекта (трассируемость) ---
OP_FIND_RELATIONS = "findRelations"
# Запрос (исходящие):   <findRelations><primary_object>UID</primary_object><relation_type>..</relation_type></findRelations>
# Запрос (входящие):    <findRelations><secondary_object>UID</secondary_object><relation_type>..</relation_type></findRelations>
# Ответ:                <findRelationsResponse><output><relation>...</relation></output></findRelationsResponse>
RELATIONS_PATH = ("findRelationsResponse", "output", "relation")

# --- Item/setProperties: ЗАПИСЬ атрибутов (write-back правки в TC) ---
OP_SET_PROPERTIES = "setProperties"
# Запрос: <setProperties><input><object>UID</object><properties><object_string>..</object_string></properties></input></setProperties>
SET_PROPERTIES_PATH = ("setPropertiesResponse", "output")

# ═══════════════════════════ Имена атрибутов объектов TC ═══════════════════════════
# (localname элементов внутри <item_revision> / <item>)
ATTR_UID = "uid"                    # атрибут uid у элементов-объектов
ATTR_ITEM_ID = "item_id"
ATTR_ITEM_REVISION_ID = "item_revision_id"
ATTR_OBJECT_NAME = "object_name"
ATTR_OBJECT_STRING = "object_string"   # текст требования (RMS хранит его здесь)
ATTR_TYPE_NAME = "type_name"
ATTR_OWNING_USER = "owning_user"
ATTR_LAST_MODIFIED = "last_modified"

# Типы объектов (type_name), с которыми работает синхронизация
TYPE_SPECIFICATION = "Specification"
TYPE_SPEC_SECTION = "SpecSection"
TYPE_REQUIREMENT = "RequirementRevision"

# ═══════════════════════════ Связи ═══════════════════════════
REL_SPEC_CONTENT = "IMAN_specification"              # контент (HTML-датасет)
REL_TRACE = "TC_Requirement_Trace_Relation"          # трассируемость требований
REL_SPEC_REFERENCE = "TC_Requirement_Spec_Reference" # раздел -> требование

# ═══════════════════════════ Типы датасетов с текстовым контентом ═══════════════════════════
DATASET_TYPE_HTML = "HTMLDataset"
DATASET_TYPE_TEXT = "TextDataset"

# Формат файла контента (расширение) -> MIME, по которому решаем, как читать
CONTENT_MIME_HTML = "text/html"
CONTENT_MIME_TEXT = "text/plain"
