# Сервис «Проверка чертежа» (Загрузка чертежа → Запустить проверку)

Отдельный компонент AviaProofAI: конструктор загружает чертёж, сервис
распознаёт текст (OCR-ансамбль), сверяет параметры с требованиями (LLM или
эвристики) и показывает замечания + нормальные логи каждого шага.

## Порты (как в окружении заказчика)

| Компонент | Порт | Адрес |
|---|---|---|
| UI (Streamlit) | 8501 | http://localhost:8501 |
| API (FastAPI) | 8502 | внешний: http://localhost:8502 · внутренний (docker): http://backend:8502 |

**Типичная причина «проверка не запускается» — адрес API:**
браузер не может обратиться к `http://backend:8502` (это имя внутри
docker-сети). UI должен использовать **внешний** адрес `http://localhost:8502`.
На вкладке «Диагностика» есть кнопка «Проверить соединение», которая
проверяет оба адреса и подсказывает, где проблема.

## Быстрый старт

```bash
pip install -r requirements.txt -r drawing_check/requirements.txt

# API (8502)
uvicorn drawing_check.server:app --host 0.0.0.0 --port 8502

# UI (8501) — в другом терминале
streamlit run drawing_check/ui.py --server.port 8501 --server.address 0.0.0.0
```

Или в docker: `docker compose -f drawing_check/docker-compose.yml up`.

## Конфигурация (переменные окружения)

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `API_PORT` / `UI_PORT` | 8502 / 8501 | порты |
| `API_URL_INTERNAL` | http://backend:8502 | адрес API для UI внутри docker-сети |
| `API_URL_EXTERNAL` | http://localhost:8502 | адрес API для браузера |
| `MODEL_BASE_URL` | (пусто) | OpenAI-совместимый endpoint (vLLM/Ollama/gemma). Пусто → эвристический режим |
| `MODEL_API_KEY` | (пусто) | ключ, если endpoint требует |
| `MODEL_NAME` | unsloth/gemma-4-12b-it | имя модели |
| `MODEL_CTX` | 8192 | контекст модели |
| `OCR_MODE` | auto | auto — использовать установленные движки; none — заглушка |
| `OCR_ENSEMBLE` | true | объединять тексты всех движков |
| `OCR_MAX_SIDE` | 768 | предобработка изображения (VRAM 16GB · 768px) |
| `LOG_DIR` | drawing_check/data/logs | файл логов |
| `LOG_MAX_BYTES` | 2 МБ | кольцевое усечение лога |

## OCR-ансамбль

Порядок: `tesseract` → `easyocr` → `paddleocr` (какие доступны — видно в
`/api/info` и в логах). Если ни одного нет — заглушка: распознаёт только
эталонный демо-чертёж `drawing_check/fixtures/sample_drawing.png`
(сверка по sha256); для любых других чертежей нужен движок или ручной ввод
текста (есть в UI). Установка движков:

```bash
sudo apt install tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng
pip install easyocr        # или paddleocr
```

## API

| Метод/путь | Назначение |
|---|---|
| `GET /health` | живость |
| `GET /api/info` | диагностика: адреса, модель, OCR-движки, файл логов |
| `POST /api/check` (multipart: `file`, `text`?) | загрузка чертежа + проверка |
| `GET /api/logs?limit=N` | хвост логов (JSON) |
| `GET /api/logs/raw?limit=N` | логи текстом (для тикетов) |
| `POST /api/logs/clear` | **стереть логи** (кнопка в UI) |

Пример:

```bash
curl -X POST http://localhost:8502/api/check \
     -F "file=@drawing_check/fixtures/sample_drawing.png"
# -> {"run_id":…, "ok": true, "engine": "heuristic",
#     "findings": [{"requirement_id":"REQ-1201","severity":"critical", …}], …}
```

## Логи

Каждый шаг проверки пишется в файл (JSON-строки) и отдаётся на страницу
«Логи» в UI: `Проверка запущена → OCR завершён → Запуск проверки по
требованиям → Проверка завершена` (или ошибка с текстом исключения). Это
позволяет сразу видеть, где застряло: OCR (нет движков), LLM (endpoint
недоступен — в логах WARN и фолбэк), сеть/порты (вкладка «Диагностика»).

Старые логи больше не мешают: кнопка **«Стереть логи»** (в сайдбаре UI и
`POST /api/logs/clear`) полностью очищает файл.

## Тесты

```bash
python -m pytest tests/test_drawing_check.py -q   # 13 тестов
```

## Требования для реальной проверки

В `drawing_check/llm.py` захардкожены эталонные требования (тормозная
система). Для боевого использования подключите требования из БД AviaProofAI:
замените `REQUIREMENTS` на выборку из `requirements` (пример — в докстринге
модуля) или передавайте их в запросе.
