#!/usr/bin/env bash
# Автокоммит изменений (по требованию ТЗ «при изменениях автоматически комить и пуш»).
# Запуск:  bash scripts/autocommit.sh          — однократно
#          bash scripts/autocommit.sh --watch  — каждые 60 c проверять git status
set -euo pipefail
cd "$(dirname "$0")/.."

commit_push() {
    if git diff --quiet && git diff --cached --quiet; then
        return 0
    fi
    git add -A
    git commit -q -m "chore: автосохранение изменений ($(date '+%Y-%m-%d %H:%M:%S'))" || true
    git push -q origin HEAD 2>/dev/null || echo "!! push не удался (проверьте сеть/remote)"
    echo "== автосохранение выполнено: $(git rev-parse --short HEAD)"
}

if [[ "${1:-}" == "--watch" ]]; then
    while true; do commit_push; sleep 60; done
else
    commit_push
fi
