# План AgoraLar (продукт)

Нормативных документов по модели здесь нет: модель описана в ядре
(`AxisLar/lambda/DOMAIN.md`). Здесь — про оболочку.

## 0. Статус (2025-11-25)

**Текущая версия — 0.14.1** на ядре `axislar-core` 0.10.0 (contract 5).
Выполнено: P0–P5 и сверх плана — HTTP, история прогонов, периодический
`watch`, провенанс и reuse производных фактов. Плагины: `json`, `sqlite`
(0.5.0), `filesystem` (0.3.0), `sql` (0.2.0), `http` (0.2.0), анализатор
`text` (0.1.0) — все `requires_contract >=5,<6`.

В 0.10.0 добавлено: параметры источника из `[plugins.<имя>]` и `--option`
(ADR-0015) и плагин-источник `sql` (sqlite, только чтение; `NULL` = отсутствие
значения, `content_hash` по сопоставленным колонкам). В 0.11.0 — источник
`http`: JSON-массив записей, та же семантика раскладки и разреженности
(ADR-0016). В 0.12.0 — артефакты поверх контракта 5 ядра (ADR-0019 ядра):
CLI `artifact put/get/ls/rm`, HTTP `/artifacts`, бэкенды `json`/`sqlite`
хранят байты рядом с объектами. В 0.13.0 — HTTP с bearer-токеном и
контейнером (ADR-0017): без токена только loopback, `/health` открыт,
образ собирается из wheel. В 0.14.0 — анализаторы как плагины (ADR-0018):
`PluginKind.ANALYZER`, `agoralar analyze`, плагин `text` 0.1.0.

## 1. Что уже есть

| Возможность | Где |
|---|---|
| Реестр плагинов (entry points, проверка контракта, отклонённые с причиной) | `nu/beta/public/registry.py` (ADR-0011) |
| JSON- и SQLite-бэкенды | `plugins/store_json`, `plugins/store_sqlite` (ADR-0012) |
| Источник `filesystem` (оси по `levels`, код из имени, хэш, id по политике ядра) | `plugins/source_filesystem` |
| Источник `sql` (таблица/`SELECT` в sqlite, раскладка колонок по осям, `NULL` = разреженность) | `plugins/source_sql` (ADR-0015) |
| Источник `http` (JSON-API: путь к массиву, те же правила раскладки, разреженность, хэш) | `plugins/source_http` (ADR-0016) |
| CLI: `schema`, `put`, `show`, `query`, `plugins list`, `store show/use`, `sync`, `watch`, `serve` | `nu/gamma/_internal/cli.py` |
| HTTP JSON API (`/health`, `/schema`, `/axes`, `/scalars`, `/objects/{id}`, `/query`, `/plugins`, `/artifacts`) с bearer-токеном | `nu/gamma/public/http_api.py` (ADR-0014, ADR-0017) |
| Контейнер: `Dockerfile`, `make docker-context`/`docker-build` | корень репозитория (ADR-0017) |
| Конфиг `agoralar.toml` + история прогонов `.agoralar/sync.json` | `nu/iota`, `nu/beta/public/sync_state.py` |
| Провенанс фасетов и reuse по `content_hash` | через ядро 0.7/0.8 |
| Артефакты: CLI `artifact ...`, HTTP `/artifacts`, `put --artifact role=hash` | поверх ядра 0.9.x (ADR-0019 ядра) |
| Анализаторы: `agoralar analyze`, плагин `text`, провенанс в `derived_by` | `nu/beta/public/analyzers.py`, `plugins/analyzer_text` (ADR-0018) |

## 2. Не цели

- Модель, валидация, семантика запросов — ядро.
- ORM, миграции, конкурентная запись, полноценный SQL-пушдаун.
- Веб-UI, мобильные клиенты (только CLI и HTTP).
- Собственная плагинная платформа с песочницей: плагины доверенные.

## 3. Что дальше (по приоритету)

1. **PyPI** — когда продукт стабилизируется (отложено Пользователем):
   снимет path-зависимости и вернёт честные `dependencies` в метаданные.
2. **BotCore/HermesLar** — отдельный проект; карта в
   `VaultLar/.dsh/FUTURE-bots.md`.
3. **Анализатор артефактов** — сделано в 0.14.0 (ADR-0018); следующий шаг —
   анализаторы изображений/эмбеддингов тем же контрактом.

## 4. Риски

| Риск | Признак | Что делаем |
|------|---------|------------|
| Продукт начнёт «дописывать» модель | валидация в CLI/HTTP/бэкендах | проверки только через `axislar_core.contracts` |
| Реестр превратится в платформу | манифесты, песочница, версии API | только `requires_contract` и entry points |
| Приватное ядро блокирует установку | нет PyPI | до публикации — wheel из `AxisLar/dist` (`make core`) |
| Рассинхрон версий контракта | плагины отклоняются | контракт поднимается явно, плагины и продукт обновляются в одном проходе |
