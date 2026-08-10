#!/usr/bin/env bash
# Демо AviaProofAI на заглушке Teamcenter: поднимает сервисы, гоняет полный цикл.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> 1. Заглушка Teamcenter на :9080"
uvicorn stub_tc.server:app --port 9080 >/tmp/stub_tc.log 2>&1 &
STUB_PID=$!
trap "kill $STUB_PID 2>/dev/null || true" EXIT
sleep 1

echo "==> 2. Сервис аналитики на :8080"
python -m app.cli serve >/tmp/avia_api.log 2>&1 &
API_PID=$!
trap "kill $API_PID $STUB_PID 2>/dev/null || true" EXIT
sleep 2

echo "==> 3. Синхронизация спецификации SPEC-BRAKE-001"
python -m app.cli sync --spec SPEC-BRAKE-001

echo "==> 4. Анализ: аудит качества, противоречия, трассируемость"
python -m app.cli audit
python -m app.cli conflicts
python -m app.cli traceability

echo "==> 5. Дашборд"
python -m app.cli dashboard

echo
echo "Демо завершено. API: http://127.0.0.1:8080/docs  Заглушка TC: http://127.0.0.1:9080/admin"
