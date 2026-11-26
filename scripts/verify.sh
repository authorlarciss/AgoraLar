#!/usr/bin/env bash
# Проверка на практике: собрать артефакты, поставить их в чистый venv и
# прогнать сценарии, которые видит пользователь.
#
#   scripts/verify.sh            # CLI + HTTP
#   scripts/verify.sh --docker   # ещё и образ (сборка + запуск)
#
# Требуется: uv, curl, python3 и соседний репозиторий ядра (по умолчанию
# ../AxisLar, можно задать AXISLAR_DIR). Скрипт ничего не пишет в репозиторий:
# всё происходит во временном каталоге.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
AXISLAR_DIR=${AXISLAR_DIR:-$ROOT/../AxisLar}
WITH_DOCKER=0
[[ "${1:-}" == "--docker" ]] && WITH_DOCKER=1

WORK=$(mktemp -d)
STARTED_SERVER=""
STARTED_CONTAINER=""
cleanup() {
    [[ -n "$STARTED_SERVER" ]] && kill "$STARTED_SERVER" 2>/dev/null || true
    [[ -n "$STARTED_CONTAINER" ]] && docker rm -f "$STARTED_CONTAINER" >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT

step() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
expect() { # expect <описание> <файл с выводом> <подстрока>
    grep -q -- "$3" "$2" || fail "$1: ожидалось «$3» в выводе"
    printf '  ok: %s\n' "$1"
}

VENV="$WORK/venv"
PY="$VENV/bin/python"
CLI="$VENV/bin/agoralar"

step "Версии"
uv --version
"$VENV/bin/python" --version 2>/dev/null || true
echo "репозиторий: $ROOT"
echo "ядро: $AXISLAR_DIR"

step "Колесо ядра"
CORE_WHEEL=$(ls "$AXISLAR_DIR"/dist/axislar_core-*.whl 2>/dev/null | sort | tail -1 || true)
if [[ -z "$CORE_WHEEL" ]]; then
    echo "колеса нет, собираю (make dist в $AXISLAR_DIR)..."
    (cd "$AXISLAR_DIR" && uv sync --extra dev >/dev/null && make dist >/dev/null)
    CORE_WHEEL=$(ls "$AXISLAR_DIR"/dist/axislar_core-*.whl | sort | tail -1)
fi
echo "  $CORE_WHEEL"

step "Сборка продукта и плагинов"
(cd "$ROOT" && make dist >/dev/null)
for plugin in "$ROOT"/plugins/*/; do
    (cd "$plugin" && uv build --out-dir dist >/dev/null)
done
echo "  продуктовое колесо: $(ls "$ROOT"/dist/*.whl | tail -1)"
echo "  плагинов: $(ls "$ROOT"/plugins/*/dist/*.whl | wc -l | tr -d ' ')"

step "Чистое окружение"
uv venv "$VENV" >/dev/null
uv pip install --python "$PY" --no-deps \
    "$CORE_WHEEL" "$ROOT"/dist/*.whl "$ROOT"/plugins/*/dist/*.whl >/dev/null
uv pip install --python "$PY" "pydantic>=2.0" "packaging>=24.0" >/dev/null
"$PY" -c "import axislar_core, agora_nu.mu.public as m; print(f'  ядро {axislar_core.__version__} (contract {axislar_core.CONTRACT_VERSION}), продукт {m.__version__}')"

step "Сценарий: плагины и схема"
mkdir -p "$WORK/run"
cd "$WORK/run"
printf 'store = "json"\ndata = "vault.json"\n' >agoralar.toml

"$CLI" plugins list >plugins.txt
expect "все виды плагинов видны" plugins.txt "analyzer"
expect "источник sql" plugins.txt "sql (source"
expect "хранилище json" plugins.txt "json (storage"

"$CLI" schema add-axis --name author --kind categorical
"$CLI" schema add-axis --name year --kind measurable
"$CLI" schema add-scalar --name title

step "Сценарий: источник sql → хранилище"
"$PY" - <<'PY'
import sqlite3
c = sqlite3.connect("export.db")
c.execute("CREATE TABLE edits (id TEXT, title TEXT, author TEXT, year INTEGER)")
c.executemany(
    "INSERT INTO edits VALUES (?, ?, ?, ?)",
    [("e-1", "Alpha", "ann", 1999), ("e-2", "Beta", "bob", 2004)],
)
c.commit()
c.close()
PY
"$CLI" sync --source sql --source-id db --option "path=$PWD/export.db" --option table=edits \
    --option 'axes=["author"]' --option 'measurable=["year"]' --option 'scalars=["title"]' \
    --apply >sync.txt
expect "sync применил план" sync.txt "applied: 2"
"$CLI" query --filter author:contains=ann >query.txt
expect "запрос по оси вернул объект" query.txt "total=1"

step "Сценарий: артефакт, бинарник и анализатор"
head -c 256 /dev/urandom >bin.dat
"$CLI" artifact put bin.dat --media-type application/octet-stream >bin.txt
expect "бинарный артефакт принят" bin.txt "256B"

printf 'x%.0s' $(seq 1 600) >doc.txt
DOC_HASH=$("$CLI" artifact put doc.txt --media-type text/plain | cut -d' ' -f1)
"$CLI" put --id d-1 --kind note --artifact "original=$DOC_HASH" >/dev/null
"$CLI" analyze --analyzer text --output-role preview --apply >analyze.txt
expect "анализатор сделал preview" analyze.txt "applied: 1"
"$CLI" show --id d-1 --json >entity.json
"$PY" - <<'PY'
import json
data = json.load(open("entity.json"))
preview = data["artifacts"]["preview"]
assert preview["size"] < 600, "preview должен быть короче исходника"
assert preview["derived_by"]["plugin"] == "text", "провенанс обязателен"
print(f"  ok: preview {preview['size']}B, derived_by={preview['derived_by']['plugin']}@{preview['derived_by']['version']}")
PY
"$CLI" analyze --analyzer text --output-role preview --apply >again.txt
expect "повторный прогон идемпотентен" again.txt "up to date: 1"

step "Сценарий: HTTP с токеном"
PORT=$("$PY" - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)
PORT=$PORT "$CLI" --store json --data http-vault.json serve --host 127.0.0.1 --port "$PORT" --token s3cret >serve.log 2>&1 &
STARTED_SERVER=$!
for _ in $(seq 1 50); do
    curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
    sleep 0.1
done
CODE_NO_TOKEN=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/schema")
CODE_WITH_TOKEN=$(curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer s3cret' "http://127.0.0.1:$PORT/schema")
HEALTH=$(curl -s "http://127.0.0.1:$PORT/health")
[[ "$CODE_NO_TOKEN" == "401" ]] || fail "без токена ожидался 401, получен $CODE_NO_TOKEN"
[[ "$CODE_WITH_TOKEN" == "200" ]] || fail "с токеном ожидался 200, получен $CODE_WITH_TOKEN"
case "$HEALTH" in *'"store"'*) ;; *) fail "health не отдал store: $HEALTH" ;; esac
printf '  ok: /health открыт, /schema 401 без токена и 200 с токеном\n'
kill "$STARTED_SERVER" 2>/dev/null || true
STARTED_SERVER=""

if [[ "$WITH_DOCKER" == "1" ]]; then
    step "Сценарий: контейнер"
    command -v docker >/dev/null || fail "docker не найден"
    (cd "$ROOT" && make docker-context >/dev/null && make docker-build >/dev/null)
    STARTED_CONTAINER=agoralar-verify
    docker rm -f "$STARTED_CONTAINER" >/dev/null 2>&1 || true
    docker run -d --name "$STARTED_CONTAINER" -p 18000:8000 -e AGORALAR_TOKEN=s3cret \
        -v "agoralar-verify-data:/data" agoralar:latest >/dev/null
    for _ in $(seq 1 100); do
        curl -fsS http://127.0.0.1:18000/health >/dev/null 2>&1 && break
        sleep 0.2
    done
    [[ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18000/schema)" == "401" ]] \
        || fail "контейнер: /schema без токена не 401"
    [[ "$(curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer s3cret' http://127.0.0.1:18000/schema)" == "200" ]] \
        || fail "контейнер: /schema с токеном не 200"
    printf '  ok: образ поднялся, токен обязателен\n'
    docker rm -f "$STARTED_CONTAINER" >/dev/null
    STARTED_CONTAINER=""
    docker volume rm agoralar-verify-data >/dev/null 2>&1 || true
fi

step "Итог"
echo "все проверки пройдены"
