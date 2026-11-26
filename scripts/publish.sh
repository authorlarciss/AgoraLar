#!/usr/bin/env bash
# Публикация в индекс в правильном порядке: ядро → плагины → продукт.
#
#   scripts/publish.sh --dry-run              # собрать и проверить, не загружать
#   scripts/publish.sh --index testpypi       # пробная публикация
#   scripts/publish.sh --index pypi           # боевой PyPI
#
# Токен — из UV_PUBLISH_TOKEN (не печатается и не хранится в репозитории).
# Для PyPI нужен аккаунт с 2FA и API-token; у TestPyPI токен отдельный.
# Повторный запуск безопасен: --check-url пропускает уже загруженные файлы.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
AXISLAR_DIR=${AXISLAR_DIR:-$ROOT/../AxisLar}

INDEX=pypi
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --index) INDEX="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        *) echo "неизвестный аргумент: $1" >&2; exit 2 ;;
    esac
done

case "$INDEX" in
    pypi) UPLOAD_URL="https://upload.pypi.org/legacy/"; CHECK_URL="https://pypi.org/simple/" ;;
    testpypi) UPLOAD_URL="https://test.pypi.org/legacy/"; CHECK_URL="https://test.pypi.org/simple/" ;;
    *) echo "index должен быть pypi или testpypi, получено: $INDEX" >&2; exit 2 ;;
esac

step() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

step "Сборка"
(cd "$AXISLAR_DIR" && make dist >/dev/null)
(cd "$ROOT" && make dist-all >/dev/null)

CORE_FILES=("$AXISLAR_DIR"/dist/axislar_core-*.whl "$AXISLAR_DIR"/dist/axislar_core-*.tar.gz)
PRODUCT_FILES=("$ROOT"/dist/agoralar-*.whl "$ROOT"/dist/agoralar-*.tar.gz)
PLUGIN_FILES=("$ROOT"/dist/agoralar_*.whl)

step "Проверка метаданных (twine check)"
uvx twine check "${CORE_FILES[@]}" "${PRODUCT_FILES[@]}" "${PLUGIN_FILES[@]}" | tail -3

if [[ "$DRY_RUN" == "1" ]]; then
    step "Dry run: файлы готовы, загрузка не выполнялась"
    printf '  ядро:    %s\n' "${CORE_FILES[@]##*/}"
    printf '  плагины: %s\n' "${PLUGIN_FILES[@]##*/}"
    printf '  продукт: %s\n' "${PRODUCT_FILES[@]##*/}"
    echo "  индекс для боевой публикации: $UPLOAD_URL"
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

publish() { # publish <описание> <файлы...>
    local what="$1"; shift
    step "Публикация: $what"
    uv publish --publish-url "$UPLOAD_URL" --token "$UV_PUBLISH_TOKEN" \
        --check-url "$CHECK_URL" "$@" 2>&1 | tail -5
}

# Порядок важен: пакеты ссылаются друг на друга по именам.
publish "ядро axislar-core" "${CORE_FILES[@]}"
publish "плагины" "${PLUGIN_FILES[@]}"
publish "продукт agoralar" "${PRODUCT_FILES[@]}"

step "Готово"
echo "проверить установку: uv pip install agoralar"
