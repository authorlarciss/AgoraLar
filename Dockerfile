# Образ продукта AgoraLar: HTTP-сервис с токеном.
#
# Ядро и плагины не публикуются на PyPI, поэтому в контекст сборки кладутся
# готовые wheel (см. `make docker-context`). Внутри — только установка:
# ни исходников репозитория, ни тулинга разработки.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Все пакеты экосистемы — из локальных артефактов; зависимости ставим отдельно,
# потому что у продукта и плагинов они не разрешаются через PyPI.
COPY wheels/ /wheels/
RUN pip install --no-cache-dir --no-deps /wheels/*.whl \
    && pip install --no-cache-dir "pydantic>=2.0" "packaging>=24.0" \
    && rm -rf /wheels

# Непривилегированный пользователь; данные — в его томе /data.
RUN useradd --create-home --uid 10001 agoralar \
    && mkdir -p /data \
    && chown agoralar:agoralar /data
USER agoralar

# Токен — из окружения (AGORALAR_TOKEN); без него контейнер не поднимется
# на 0.0.0.0 (CLI отказывает — fail closed).
VOLUME ["/data"]
EXPOSE 8000

# --store/--data — глобальные опции, поэтому идут до подкоманды.
CMD ["agoralar", "--store", "json", "--data", "/data/vault.json", \
     "serve", "--host", "0.0.0.0", "--port", "8000"]
