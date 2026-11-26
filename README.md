# AgoraLar

Продуктовая оболочка ядра [AxisLar](https://github.com/authorlarciss/AxisLar):
интерфейсы, реестр плагинов и готовые хранилища. Отдельный проект на базе
[BaseLar](https://github.com/authorlarciss/BaseLar) v0.7.4.

Ядро отвечает за модель, проверки и запросы. AgoraLar даёт то, чем
пользуется человек: CLI (позже HTTP), конфиг, состояние и подключение
сторонних бэкендов через entry points с проверкой `CONTRACT_VERSION`.

## Что делает продукт

- **Интерфейсы.** CLI и HTTP поверх `axislar_core.contracts`: схема,
  запись объектов, запросы, плагины. Никакой доменной логики — только
  разбор ввода и вывод (`agoralar serve` поднимает JSON API).
- **Реестр плагинов.** Находит установленные бэкенды хранилищ и источников
  через entry points, проверяет совместимость с версией контракта ядра,
  даёт внятную ошибку вместо падения.
- **Готовые хранилища.** В комплекте два бэкенда: `json` (файл) и
  `sqlite` (durable). Оба проходят контрактный набор ядра.
- **Источники.** `filesystem` (дерево каталогов → оси, имя файла → фасеты),
  `sql` (таблица или `SELECT` в sqlite) и `http` (JSON-API). `sync` приводит
  хранилище в соответствие с источником (создание, переименование по хэшу,
  tombstone), `watch` делает это периодически и помнит историю прогонов.
  Параметры источника берутся из `[plugins.<имя>]` в `agoralar.toml` или из
  `--option key=value`; продукт их не перечисляет — их знает плагин.
- **Артефакты.** Байты (превью, эмбеддинги) адресуются хэшем содержимого
  и делятся между объектами: `artifact put/get/ls/rm` и маршруты
  `/artifacts` в HTTP. Ссылка живёт в объекте (`put --artifact role=hash`),
  сами байты — отдельно.
- **Анализаторы.** Производные артефакты делают плагины вида `analyzer`
  (`agoralar analyze`): входная роль → роль, названная анализатором, с
  провенансом `plugin@version`. В комплекте — `text` (предпросмотр).
- **Безопасность HTTP.** Bearer-токен (`--token` или `AGORALAR_TOKEN`);
  без токена сервер слушает только loopback, открыт лишь `/health`
  (ADR-0017).
- **Конфиг и состояние.** Где лежат данные, какой бэкенд активен, какие
  плагины включены.

## Чего продукт не делает

- Не реализует модель: оси, фасеты, объекты, семантику запросов — это
  ядро. Продукт не «дописывает» правила.
- Не тянет ядро в свой пакет: зависимость только от контракта
  (`axislar_core.contracts`).
- Не содержит UI-специфики в бэкендах: плагин хранилища не знает, что его
  вызвал CLI.

## Структура

| Модуль | Роль |
|--------|------|
| `alpha` | DTO продукта: конфиг, манифест плагина |
| `beta` | Реестр плагинов: поиск, проверка контракта, включение |
| `gamma` | Интерфейсы: CLI и HTTP |
| `iota` | Утилиты: пути, разбор настроек |
| `psi` | Сборка продукта: `build_product(core, registry)` |

| `mu` | Версия продукта и поддерживаемый диапазон контракта ядра |

Плагины — отдельные пакеты в `plugins/`: `agoralar-store-json`,
`agoralar-store-sqlite`, `agoralar-source-filesystem`, `agoralar-source-sql`,
`agoralar-source-http`, `agoralar-analyzer-text`.
Продукт находит их через entry points (`agoralar.storage`, `agoralar.source`)
и проверяет совместимость с `CONTRACT_VERSION` ядра; сами плагины зависят
только от `axislar-core`.

## Быстрый старт

```bash
make install            # venv + dev-зависимости

make core               # ядро из локального wheel ../AxisLar/dist (репозиторий приватный)
# плагины ставятся как path-зависимости при uv sync (plugins/*)
# или из артефакта релиза:
# gh release download v0.8.0 -R authorlarciss/AxisLar -p '*.whl' -D /tmp/core
# uv pip install /tmp/core/axislar_core-0.8.0-py3-none-any.whl

make check              # линт, контракты, типы, тесты
```

## Как проверить состояние

```bash
make check          # линт, типы, импорт-контракты, тесты (нужен ядро в .venv)
make verify         # артефакты → чистый venv → сценарии CLI и HTTP
make verify-docker  # то же плюс сборка и запуск контейнера
```

`make verify` ничего не пишет в репозиторий: собирает колесо ядра (или берёт
готовое из `../AxisLar/dist`), колесо продукта и плагинов, ставит их в
временный venv и прогоняет то, что видит пользователь: `plugins list`, схему,
`sync --source sql`, `artifact put` (включая бинарник), `analyze` с
провенансом, повторный прогон на идемпотентность и HTTP с токеном
(`/health` открыт, `/schema` — 401/200). Нужны `uv`, `curl`, `python3` и
соседний репозиторий ядра (`AXISLAR_DIR`).

## CI

`.github/workflows/ci.yml` разделён на два уровня, потому что ядро
(`axislar-core`) живёт в приватном репозитории `AxisLar`:

| Job | Что проверяет | Когда работает |
|---|---|---|
| `lint` | `ruff format --check`, `ruff check` | всегда (ядро не нужно) |
| `test` | `make check` (mypy, import-linter, pytest) | если задан секрет `AXISLAR_TOKEN` |
| `dist` | сборка колеса и прикрепление к релизу | теги `v*` + `AXISLAR_TOKEN` |

One-time настройка (Settings → Secrets and variables → Actions): создать
PAT с доступом на чтение к `authorlarciss/AxisLar` и положить его в
`AXISLAR_TOKEN`. Без секрета CI не падает молча: job пишет предупреждение
«секрет AXISLAR_TOKEN не задан — mypy/pytest пропущены», то есть видно, что
именно не проверено.

## HTTP и контейнер

```bash
# локально, без токена — только loopback
agoralar serve

# наружу — с токеном
export AGORALAR_TOKEN=...
agoralar serve --host 0.0.0.0 --port 8000

# образ: wheel-ы ядра, продукта и плагинов кладутся в контекст
make dist && (cd ../AxisLar && make dist) && make docker-context
make docker-build
docker run --rm -p 8000:8000 -e AGORALAR_TOKEN=... -v agoralar-data:/data agoralar
```

Токен обязателен для любого не-loopback адреса: `serve` откажет (код 2).
Контейнер работает от `uid 10001`, поэтому именованный том — рабочий
вариант; bind-mount хостового каталога нужно либо заранее отдать этому
uid, либо запускать с `--user $(id -u):$(id -g)`. TLS, лимиты и ротация —
за обратным прокси (ADR-0017).

## Статус

`0.14.1`: ядро 0.10.0 (клиенты/фичи), реестр плагинов, бэкенды `json`/`sqlite` с артефактами,
HTTP с bearer-токеном и образом в `Dockerfile`, анализатор текста,
источники `filesystem`, `sql` и `http`, CLI (`sync`, `watch`, `serve` — HTTP), конфиг `agoralar.toml`,
история прогонов, работа на ядре 0.8.0 (провенанс, reuse по хэшу).
План — `lambda/PLAN.md`, решения — `lambda/adr/`.
