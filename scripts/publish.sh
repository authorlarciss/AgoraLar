#!/usr/bin/env bash
# Публикация в индекс в правильном порядке: ядро → плагины → продукт.
#
#   scripts/publish.sh --dry-run              # собрать и проверить, не загружать
#   scripts/publish.sh --index testpypi       # пробная публикация
#   scripts/publish.sh --index pypi           # боевой PyPI
#   scripts/publish.sh --index pypi --resume  # продолжить: без пересборки,
#                                             # уже опубликованные проекты пропустить
#
# Токен — из UV_PUBLISH_TOKEN (не печатается и не хранится в репозитории).
# Повторный запуск безопасен: существующие проекты пропускаются, «file already
# exists» считается успехом.
#
# Внимание: PyPI ограничивает создание НОВЫХ проектов для свежих аккаунтов
# (4 проекта в 24 часа). Если лимит сработал — подождать сутки и запустить
# с --resume, либо попросить исключение в https://github.com/pypi/support.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
AXISLAR_DIR=${AXISLAR_DIR:-$ROOT/../AxisLar}

INDEX=pypi
DRY_RUN=0
RESUME=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --index) INDEX="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        --resume) RESUME=1; shift ;;
        *) echo "неизвестный аргумент: $1" >&2; exit 2 ;;
    esac
done

case "$INDEX" in
    pypi) UPLOAD_URL="https://upload.pypi.org/legacy/"; CHECK_URL="https://pypi.org/simple/"; JSON_BASE="https://pypi.org" ;;
    testpypi) UPLOAD_URL="https://test.pypi.org/legacy/"; CHECK_URL="https://test.pypi.org/simple/"; JSON_BASE="https://test.pypi.org" ;;
    *) echo "index должен быть pypi или testpypi, получено: $INDEX" >&2; exit 2 ;;
esac

step() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

version_of() { # version_of <wheel> -> версия из METADATA внутри колеса
    python3 - "$1" <<'PY'
import sys, zipfile
from pathlib import Path
with zipfile.ZipFile(Path(sys.argv[1])) as wheel:
    metadata = [name for name in wheel.namelist() if name.endswith("METADATA")][0]
    for line in wheel.read(metadata).decode().splitlines():
        if line.startswith("Version: "):
            print(line.split(": ", 1)[1])
            break
PY
}

published() { # published <проект> <версия> -> 0, если версия уже в индексе
    curl -fsS -o /dev/null "$JSON_BASE/pypi/$1/$2/json" 2>/dev/null
}

if [[ "$RESUME" == "0" ]]; then
    step "Сборка"
    (cd "$AXISLAR_DIR" && make dist >/dev/null)
    (cd "$ROOT" && make dist-all >/dev/null)
else
    step "Сборка пропущена (--resume): берём готовые файлы из dist/"
    [[ -n "$(ls -1 "$ROOT"/dist/*.whl 2>/dev/null)" ]] || { echo "нет файлов в dist/ — нужен обычный запуск" >&2; exit 2; }
fi

# project|glob — по одной записи на публикуемый проект; порядок важен.
# Имя массива не GROUPS: в bash это встроенная переменная со списком групп.
PUBLISH_TARGETS=(
    "axislar-core|$AXISLAR_DIR/dist/axislar_core-*"
    "agoralar-store-json|$ROOT/dist/agoralar_store_json-*"
    "agoralar-store-sqlite|$ROOT/dist/agoralar_store_sqlite-*"
    "agoralar-source-filesystem|$ROOT/dist/agoralar_source_filesystem-*"
    "agoralar-source-sql|$ROOT/dist/agoralar_source_sql-*"
    "agoralar-source-http|$ROOT/dist/agoralar_source_http-*"
    "agoralar-analyzer-text|$ROOT/dist/agoralar_analyzer_text-*"
    "agoralar|$ROOT/dist/agoralar-[0-9]*"
)

step "Проверка метаданных (twine check)"
files=()
for target in "${PUBLISH_TARGETS[@]}"; do
    for file in ${target#*|}; do
        [[ -e "$file" ]] && files+=("$file")
    done
done
[[ ${#files[@]} -gt 0 ]] || { echo "нет файлов для публикации" >&2; exit 2; }
uvx twine check "${files[@]}" | tail -2

if [[ "$DRY_RUN" == "1" ]]; then
    step "Dry run: файлы готовы, загрузка не выполнялась"
    printf '  %s\n' "${files[@]##*/}"
    echo "  индекс: $UPLOAD_URL"
    exit 0
fi

if [[ -z "${UV_PUBLISH_TOKEN:-}" ]]; then
    cat >&2 <<'EOF'
Нет UV_PUBLISH_TOKEN.

Создать токен: PyPI → Account settings → API tokens (или TestPyPI для пробного
прогона), затем:

    export UV_PUBLISH_TOKEN=pypi-...
    scripts/publish.sh --index testpypi
EOF
    exit 2
fi

sent=0
skipped=0
for target in "${PUBLISH_TARGETS[@]}"; do
    project="${target%%|*}"
    glob="${target#*|}"
    # shellcheck disable=SC2206
    group_files=($glob)
    [[ ${#group_files[@]} -gt 0 ]] || { echo "нет файлов для $project ($glob)" >&2; exit 2; }
    version=$(version_of "${group_files[0]}")

    if published "$project" "$version"; then
        printf '  skip: %s %s уже опубликован\n' "$project" "$version"
        skipped=$((skipped + 1))
        continue
    fi

    step "Публикация: $project $version"
    output=$(uv publish --publish-url "$UPLOAD_URL" --token "$UV_PUBLISH_TOKEN" \
        --check-url "$CHECK_URL" "${group_files[@]}" 2>&1) && status=0 || status=$?
    printf '%s\n' "$output" | tail -4
    if [[ $status -ne 0 ]]; then
        if grep -q "File already exists" <<<"$output"; then
            echo "  уже был загружен — считаем успехом"
            skipped=$((skipped + 1))
            continue
        fi
        if grep -q "429 Too many new projects" <<<"$output"; then
            echo "  PyPI: лимит на новые проекты (4 в 24 часа). Подожди и запусти с --resume." >&2
            exit 3
        fi
        exit "$status"
    fi
    sent=$((sent + 1))
done

step "Готово"
echo "опубликовано сейчас: $sent, уже было: $skipped"
echo "проверить: uv pip install agoralar"
