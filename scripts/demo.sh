#!/usr/bin/env bash
# Демо AgoraLar: от пустого каталога до запроса и связей за полминуты.
#
#   make demo            # из репозитория
#   KEEP=1 make demo     # оставить каталог демо на диске
#
# Скрипт ничего не меняет в репозитории: копирует тестовое дерево файлов во
# временный каталог и работает там. Внешние сервисы не нужны — только
# локальный sqlite-стор.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PYTHON=${PYTHON:-python3}

WORK=$(mktemp -d "${TMPDIR:-/tmp}/agoralar-demo-XXXXXX")
cleanup() {
    if [ "${KEEP:-0}" = "1" ]; then
        echo "каталог демо: $WORK"
    else
        rm -rf "$WORK"
    fi
}
trap cleanup EXIT

if [ -n "${AGORALAR_BIN:-}" ]; then
    cli() { "$AGORALAR_BIN" "$@"; }
else
    cli() { "$PYTHON" -m nu.main "$@"; }
fi

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

cp -R "$ROOT/examples/demo/library" "$WORK/library"
cd "$WORK"

step "1/6 шаблон: схема и конфиг из video-archive"
cli init --template video-archive

step "2/6 дерево файлов → объекты (источник только читает)"
cli sync --apply

step "3/6 запрос по оси: что лежит в разделе noir"
cli query --filter section:contains=noir

step "4/6 карточка одного объекта"
first=$(cli query --filter format:contains=mp4 --json |
    "$PYTHON" -c 'import json, sys; print(json.load(sys.stdin)["ids"][0])')
cli show --id "$first"

step "5/6 связи: кто ссылается на объект"
# сначала цель, потом ссылка: модель запрещает висячие ссылки
cli put --id ep-b --kind video --facet collection=manual >/dev/null
cli put --id ep-a --kind video --facet collection=manual --facet related=ep-b >/dev/null
cli links --id ep-b

step "6/6 отчёт о разреженности (автономный HTML)"
cli visualize --output report.html

printf '\n\033[1mготово\033[0m: хранилище %s/vault.sqlite3, отчёт %s/report.html\n' "$WORK" "$WORK"
echo "файлы источника не менялись: $(find "$WORK/library" -type f | wc -l | tr -d ' ') файлов на месте"
