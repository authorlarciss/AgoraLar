# AgoraLar

[![CI](https://github.com/authorlarciss/AgoraLar/actions/workflows/ci.yml/badge.svg?branch=nu)](https://github.com/authorlarciss/AgoraLar/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/agoralar)](https://pypi.org/project/agoralar/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

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
- **Источники.** `markdown` (заметки: frontmatter, `#tags`, `[[wiki-links]]`
  → reference-ось), `filesystem` (дерево каталогов → оси, имя файла → фасеты),
  `sql` (таблица или `SELECT` в sqlite) и `http` (JSON-API). `sync` приводит
  хранилище в соответствие с источником (создание, переименование по хэшу,
  tombstone), `watch` делает это периодически и помнит историю прогонов.
  Параметры источника берутся из `[plugins.<имя>]` в `agoralar.toml` или из
  `--option key=value`; продукт их не перечисляет — их знает плагин.
- **Артефакты.** Байты (превью, эмбеддинги) адресуются хэшем содержимого
  и делятся между объектами: `artifact put/get/ls/rm` и маршруты
  `/artifacts` в HTTP. Ссылка живёт в объекте (`put --artifact role=hash`),
  сами байты — отдельно.
- **Представления.** `agoralar export --group-by` строит дерево симлинков по
  значениям осей: `--multi first|all|join` для мультизначных, bucket
  `_none` для разреженных, суффикс `-<id[:8]>` при коллизиях имён.
  По умолчанию dry-run; в чужой каталог не пишет без `--force`,
  существующие ссылки не удаляет. Оригиналы не трогаются, и это не бэкап.
- **Отчёт.** `agoralar visualize` пишет автономный HTML: сколько объектов
  заполнили каждую ось, какие значения встречаются, где разреженность.
  Без сети, без внешних ресурсов, без JavaScript — файл можно переслать.
- **Связи.** Обратный индекс ссылок (`references_to`) и команда
  `agoralar links --id ...`: «кто ссылается на этот объект и по какой оси».
  Опора для будущего графа и проверок удаления.
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

Демо целиком, без чтения документации:

```bash
make demo    # шаблон → синхронизация каталога → запрос → связи, ~10 секунд
```

Своё хранилище:

```bash
agoralar init --list                      # шаблоны: video-archive, obsidian-vault
agoralar init --template video-archive    # agoralar.toml + оси в сторе
mkdir -p library/collection-a/noir/"VTA Night City"
# ... положить файлы в library/<collection>/<section>/<title>/ ...
agoralar sync --apply                     # дерево файлов → объекты (файлы не меняются)
agoralar query --filter section:contains=noir
agoralar links --id <id>                  # кто ссылается на объект

Заметки Obsidian — то же самое, но источник `markdown`:

```bash
agoralar init --template obsidian-vault    # оси: folder, tags, created, links
# ... положить .md в vault/ с [[wiki-ссылками]] и #тегами ...
agoralar sync --source markdown --apply
agoralar query --filter tags:contains=noir
```

Повторный `init` без `--force` отказывает, с `--force` только добавляет
недостающие оси: оси неизменяемы (DOMAIN A4), переименовать их нельзя.

Разработка:

```bash
make install            # venv + dev-зависимости (ядро — из PyPI)
make check              # линт, контракты, типы, тесты
make bench              # бенчмарк стора (BACKEND=sqlite|json, N=объектов)
```

## Как проверить состояние

```bash
make check          # линт, типы, импорт-контракты, тесты (нужен ядро в .venv)
make verify         # артефакты → чистый venv → сценарии CLI и HTTP
make verify-docker  # то же плюс сборка и запуск контейнера
make verify-index   # установка из PyPI и те же сценарии (после публикации)
```

`make verify` ничего не пишет в репозиторий: собирает колесо ядра (или берёт
готовое из `../AxisLar/dist`), колесо продукта и плагинов, ставит их в
временный venv и прогоняет то, что видит пользователь: `plugins list`, схему,
`sync --source sql`, `artifact put` (включая бинарник), `analyze` с
провенансом, повторный прогон на идемпотентность и HTTP с токеном
(`/health` открыт, `/schema` — 401/200). Нужны `uv`, `curl`, `python3` и
соседний репозиторий ядра (`AXISLAR_DIR`).

## CI

`.github/workflows/ci.yml` — три job'а:

| Job | Что проверяет | Когда работает |
|---|---|---|
| `lint` | `ruff format --check`, `ruff check` | всегда (ядро не нужно) |
| `test` | `make check` (mypy, import-linter, pytest) | если задан секрет `AXISLAR_TOKEN` |
| `dist` | сборка колеса и прикрепление к релизу | теги `v*` + `AXISLAR_TOKEN` |

Запустить проверку вручную: `gh workflow run ci.yml` (или кнопка
«Run workflow» в Actions). One-time настройка
(Settings → Secrets and variables → Actions): создать
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

## Установка

Установка одной командой из PyPI (продукт тянет ядро и все плагины):

```bash
uv pip install agoralar
```

Офлайн-вариант из колес релиза (если индекс недоступен):

```bash
# колеса: релиз AgoraLar содержит продукт и все плагины,
# ядро берётся из релиза AxisLar
mkdir -p wheels/axislar wheels/agoralar
gh release download v0.12.1 -R authorlarciss/AxisLar -p '*.whl' --dir wheels/axislar
gh release download v0.23.0 -R authorlarciss/AgoraLar -p '*.whl' --dir wheels/agoralar

uv venv .venv
uv pip install --python .venv/bin/python \
  --find-links wheels/axislar --find-links wheels/agoralar \
  agoralar
.venv/bin/agoralar --help
```

Публикация в индексы — отдельный шаг с твоими токенами, см.
[lambda/PUBLISHING.md](lambda/PUBLISHING.md) и `make publish-check` /
`make publish-test` / `make publish`.

Проверить, что всё на месте:

```bash
.venv/bin/agoralar plugins list        # 7 плагинов (6 источников/хранилищ + анализатор)
.venv/bin/agoralar schema show         # пустая схема
```

## Что дальше

Продуктовые направления (шаблоны, markdown-источник, симлинк-экспорт,
Web UI, cookbook) и их технические ограничения — `lambda/ROADMAP.md`.
Приоритеты и критерии готовности — `lambda/PLAN.md`, раздел «Что дальше»:
завершить публикацию в PyPI (лимит аккаунта), образ в `ghcr.io`,
бенчмарки, гайд по плагинам, пользовательская документация,
`export`/`import`, потребитель профилей клиентов.

## Интерфейс

`agoralar serve` поднимает локальный сервер и отдаёт UI на том же origin,
что и API:

```bash
agoralar serve            # http://127.0.0.1:8000 — открыть в браузере
```

Что есть в первой версии: список объектов с фильтрами `axis:op=значения`
и пагинацией, карточка объекта с JSON и связями (входящие и исходящие),
таблица разреженности по осям с предупреждением о пустых. Фильтры — это
чипы, а состояние выборки живёт в адресе страницы: ссылку можно переслать,
страницу — перезагрузить, «назад» возвращает предыдущий фильтр. Только
чтение, только loopback, без токена и без сборки — статика отдаётся из
пакета. Редактирование появится после ревизий и отката (фазы 2–3 плана).

## Скорость

`make bench BACKEND=sqlite N=1000` и `make bench BACKEND=json N=500`
(одна машина, Python 3.13, медиана из пяти замеров):

| Операция | `sqlite`, 1000 объектов | `sqlite`, 5000 объектов | `json`, 500 объектов |
|---|---|---|---|
| `upsert`, мс | 0.54 | 0.39 | 2.59 |
| обновление объекта, мс | 0.41 | 0.41 | 4.07 |
| чтение по `id`, мс | 0.13 | 0.12 | — |
| запрос по оси, мс | 0.85 | 4.59 | 0.66 |
| обход ссылок, мс | 0.15 | 0.27 | 0.01 |
| открытие хранилища, мс | 0.55 | 0.56 | 5.03 |

`sqlite` — самостоятельный бэкенд на SQL: документы целиком в память не
загружаются, запросы исполняются по индексам, страница отдаётся через
`LIMIT/OFFSET`. Поэтому открытие хранилища не зависит от числа объектов
(0.55 мс и на 1000, и на 5000), а запись — O(1). Запрос с широким фильтром
растёт с числом совпадений: он всё равно читает страницу и считает `total`.

`json` переписывает файл целиком при каждой записи: это осознанно медленный
бэкенд для переносимости и диффов, и его чтение по-прежнему поднимает весь
файл в память.

## Качество

`make check` — один гейт: ruff, mypy strict, шесть импорт-контрактов,
контрактные наборы ядра, тесты. На 2026-11-27: **404 теста**, покрытие
**93%**, шесть контрактов.

Покрытие видно без внешних сервисов: CI кладёт HTML-отчёт артефактом
`coverage-html` в каждый прогон (вкладка Actions → нужный run → Artifacts).
Бейджа Codecov сознательно нет: он показывает не наши числа, а свои.

## Лицензия

MIT — см. [LICENSE](LICENSE).

## Статус

`0.23.0`: MIT, публикация по OIDC с PEP 740-аттестацией (`/integrity/<проект>/...)`, честные зависимости, ядро 0.13.1 (контракт 6: ревизии объектов и `expected_rev`); реестр плагинов, бэкенды `json`/`sqlite` с артефактами,
HTTP с bearer-токеном и образом в `Dockerfile`, анализатор текста,
источники `markdown`, `filesystem`, `sql` и `http`, CLI (`sync`, `watch`, `serve` — HTTP), конфиг `agoralar.toml`,
история прогонов, провенанс и reuse производных фактов по `content_hash`,
обратные ссылки через `references_to`.
План — `lambda/PLAN.md`, решения — `lambda/adr/`.
