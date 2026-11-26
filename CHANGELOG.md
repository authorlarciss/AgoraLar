# Changelog

All notable changes to AgoraLar are documented in this file.

Format based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

Продукт ответвлён от шаблона BaseLar v0.7.4; историю шаблона см. в BaseLar.

## [Unreleased]

## [0.14.0] - 2025-11-25

### Added

- **Анализаторы как плагины продукта** (ADR-0018): вид
  `PluginKind.ANALYZER` и entry point `agoralar.analyzer`; контракт
  структурный (`IAnalyzer`: `version`, `describe`, `accepts`, `analyze`),
  поэтому плагин не импортирует ни ядро, ни продукт.
- `agoralar analyze --analyzer text [--input-role original]
  [--output-role preview] [--option k=v] [--force] [--apply]`: берёт
  входной артефакт, кладёт производный (`put_artifact`, дедупликация по
  хэшу) и обновляет объект ссылкой с `derived_by`. Dry run по умолчанию,
  повторный прогон ничего не пересчитывает.
- Плагин **`agoralar-analyzer-text` 0.1.0**: предпросмотр текста
  (обрезка по символам, UTF-8 с `errors="replace"`), без внешних
  зависимостей.

### Fixed

- `plugins/store_json` 0.5.0 понимает формат **2**: `Artifact` сериализуется
  base64url (ядро 0.9.1), бинарные артефакты больше не ломают запись.
  Файл версии 1 читается, только если в нём нет артефактов; иначе —
  понятная ошибка с требованием переимпорта.

## [0.13.0] - 2025-11-25

### Added

- **Аутентификация HTTP** (ADR-0017): bearer-токен из `--token` или
  `AGORALAR_TOKEN`, сравнение `secrets.compare_digest`. Без токена —
  локальный режим; `serve` на не-loopback адресе без токена отказывает
  (код 2), а не предупреждает. `/health` открыт как проба живости,
  остальные маршруты (включая `/plugins`, `/artifacts`) требуют токена.
- **Контейнер**: `Dockerfile` ставит только wheel (ядро, продукт,
  плагины) плюс `pydantic`/`packaging`, работает от непривилегированного
  пользователя, данные — в томе `/data`. `make docker-context` собирает
  контекст из артефактов, `make docker-build` — образ.

## [0.12.0] - 2025-11-25

### Added

- **Артефакты в продукте** поверх контракта 5 ядра: `agoralar artifact put
  <файл> --media-type`, `get <hash> [--out]`, `ls [--json]`, `rm <hash>`;
  `put --artifact role=hash` привязывает артефакт к объекту (метаданные
  берутся из хранилища, а не вводятся руками).
- HTTP: `GET /artifacts`, `POST /artifacts` (base64), `GET /artifacts/{hash}`,
  `DELETE /artifacts/{hash}` (409 `artifact_in_use`, если на артефакт
  ссылается объект).
- `plugins/store_json` 0.4.0 и `plugins/store_sqlite` 0.4.0 сохраняют
  артефакты рядом с объектами (ключ `artifacts` в файле; таблица
  `artifacts(hash, media_type, data BLOB)`).

### Changed

- **Ядро 0.9.0, контракт 5**: `CORE_CONTRACT_RANGE = ">=5,<6"`, все
  плагины объявляют `requires_contract >=5,<6` (`filesystem` 0.3.0,
  `sql` 0.2.0, `http` 0.2.0).

## [0.11.0] - 2025-11-25

### Added

- **Плагин-источник `http`** (`agoralar-source-http` 0.1.0): JSON-массив
  записей по URL → объекты. Путь к массиву — `items` (`data.items`), поля
  раскладываются теми же `key`/`axes`/`multi`/`measurable`/`reference`/
  `scalars`/`hash`, что у `sql` (ADR-0016). `null` = отсутствие значения,
  `content_hash` — по сопоставленным полям, транспорт только читает.
  Ошибки транспорта — `HttpSourceError` (`RuntimeError`), прогон падает
  целиком. Проходит `run_source_sync_contract`.

## [0.10.0] - 2025-11-25

### Added

- **Параметры плагина-источника: конфиг и `--option`** (ADR-0015).
  `sync`/`watch` берут настройки из `[plugins.<имя>]`, явные флаги их
  уточняют, `--option KEY=VALUE` перекрывает всё. Значение разбирается как
  TOML-скаляр (`true`, `42`, `["a","b"]`), иначе остаётся строкой. Продукт
  больше не перечисляет чужие параметры: новый источник подключается без
  правок CLI.
- **Плагин-источник `sql`** (`agoralar-source-sql` 0.1.0): таблица или
  `SELECT` в sqlite → объекты. Колонки раскладываются по осям явно
  (`axes`, `multi`, `measurable`, `reference`, `scalars`), `NULL` = нет
  значения (разреженность, ADR-0010 ядра), `content_hash` — по
  сопоставленным колонкам, `id` — по `entity_id_for(source_id, key)`.
  База открывается только на чтение. Проходит `run_source_sync_contract`.
- В выводе `sync` печатается `describe()` источника.

### Changed

- `Config.source_options_for(name)` — секция плагина-источника без
  подстановки общего `data` (это путь хранилища, а не источника).

## [0.9.0] - 2025-11-25

### Added

- Работа на ядре **0.8.0**: при синхронизации копии проекта производные
  фасеты (`analysis`) переиспользуются по `content_hash` (ADR-0017 ядра).
  Сквозной тест: оригинал с `cluster`, копия с тем же содержимым получает
  фасет без повторного анализа.

## [0.8.0] - 2025-11-25

### Added

- **HTTP-интерфейс** (`agoralar serve --host --port`, stdlib, без новых
  зависимостей): `GET /health`, `GET /schema`, `POST /axes`,
  `POST /scalars`, `GET|PUT /objects/{id}`, `POST /query`, `GET /plugins`.
  Ошибки домена — 422 с кодами, невалидный ввод — 400, нет ресурса — 404,
  неверный метод — 405; тело запроса ограничено 8 МБ.
- Маршрутизация (`HttpApi`) отделена от транспорта (`make_server`/`serve`),
  поэтому маршруты тестируются без сокетов, а сервер — на порту 0.

## [0.7.0] - 2025-11-25

### Changed

- Переход на ядро **contract 4**: провенанс фасетов (ADR-0018 ядра).
  `sync --apply` больше не затирает `user`/`analysis`-фасеты; источник
  обновляет только свои оси, а ручная правка не считается изменением
  источника.
- Плагины: `agoralar-store-json` и `agoralar-store-sqlite` 0.3.0,
  `agoralar-source-filesystem` 0.2.0 (размечает владельцев `source`);
  все объявляют `requires_contract >=4,<5`.

### Added

- Сквозные тесты провенанса: ручной фасет выживает пересканирование,
  изменение только пользовательского фасета не запускает перезапись.

## [0.6.0] - 2025-11-25

### Added

- **История прогонов синхронизации** (`SyncState`): после каждого
  применения `sync` пишет `.agoralar/sync.json` (timestamp, scanned,
  счётчики плана, число конфликтов) и печатает `previous:` — что было в
  прошлый раз. Потеря файла означает лишь потерю истории, не модели.
- **`agoralar watch`**: периодическая синхронизация (`--interval`,
  `--iterations`, `0` — бесконечно), те же флаги источника, `--apply`,
  `--state-file`.
- Ручной `--state-file` для истории (удобно в тестах и для нескольких
  источников).

## [0.5.0] - 2025-11-25

### Added

- **Источник `filesystem`** (`agoralar-source-filesystem` 0.1.0):
  файловое дерево → оси по конфигу `levels`, `title` с выделением кода
  (`VTA Cyberpunk` → `code=VTA`, `title=Cyberpunk`), автор и метка
  времени из имени, `content_hash` (size + голова/хвост),
  `id = UUIDv5(source_id, key)`. Рассогласования (код против каталога,
  неожиданная глубина) — в `warnings`, объект всё равно импортируется.
- **`agoralar sync`**: `--source`, `--root`, `--source-id`, `--levels`,
  `--code CODE=section`, `--incomplete`, `--apply` (по умолчанию dry run).
  Печатает счётчики плана, конфликты и предупреждения источника.
- Продукт зависит от ядра 0.6.x (identity policy, source sync contract).

## [0.4.0] - 2025-11-25

### Changed

- Продукт работает с ядром **contract 3**: `CORE_CONTRACT_RANGE = ">=3,<4"`.
- Плагины `agoralar-store-json` и `agoralar-store-sqlite` обновлены до
  0.2.0: реализуют `list_entities(include_deleted)` и объявляют
  `requires_contract >=3,<4`; проходят обновлённый контрактный набор ядра
  (tombstone-семантика, resurrect).

### Added

- Сквозная поддержка soft-delete: объекты с `deleted_at` не попадают в
  выдачи бэкендов, но доступны синхронизации через
  `list_entities(include_deleted=True)`.

## [0.3.0] - 2025-11-25

### Changed

- **Бэкенды вынесены в отдельные пакеты-плагины:** `agoralar-store-json`,
  `agoralar-store-sqlite` (в репозитории — `plugins/`). Продукт находит их
  через entry points `agoralar.storage`, а не через встроенный список.
- **Манифест плагина — обычные данные** (ADR-0013): точка входа возвращает
  `{"requires_contract": ..., "factory": ...}`; плагин зависит только от
  `axislar-core` и не импортирует DTO продукта. `PluginManifest` остаётся
  внутренним DTO; продукт принимает и его (обратная совместимость).
- Ошибки бэкендов больше не импортируются продуктом: CLI печатает
  `RuntimeError` от плагинов как пользовательскую ошибку.
- Продукт зависит от пакетов плагинов (до публикации в PyPI — path-зависимости
  через `[tool.uv.sources]`).

### Added

- `plugins/store_json`, `plugins/store_sqlite`: собственные pyproject,
  тесты, контрактный набор ядра; проверено установкой в чистый venv.

## [0.2.0] - 2025-11-25

### Added

- **SQLite-бэкенд** (`sqlite`) как встроенный плагин: durable-хранилище
  поверх эталонного движка ядра, `SqliteStoreError`, версия формата БД
  (ADR-0012). Пушдауна предикатов нет — семантика не дублируется.
- SQLite-бэкенд проходит контрактный набор ядра
  (`run_storage_contract`) целиком — второй независимый слой хранения
  проверен тем же набором, что и эталон.
- `make core` ставит последний wheel ядра из `../AxisLar/dist`
  (ядро 0.3.0 с контрактным набором).

### Changed

- `builtin_plugins()` теперь отдаёт два манифеста: `json` и `sqlite`;
  `plugins list` и CLI видят оба.
- SQLite-соединения закрываются явно (`closing`), тесты гоняются с
  `-W error::ResourceWarning`.

## [0.1.0] - 2025-11-25

Первый релиз продукта: оболочка над ядром `axislar-core` 0.2.x.

### Added

- **Реестр плагинов** (ADR-0011): обнаружение через entry points
  `agoralar.storage` / `agoralar.source`, проверка `requires_contract`
  против `CONTRACT_VERSION` ядра и диапазона продукта, отклонённые
  плагины с причиной, ленивое создание через фабрику.
- **Встроенный JSON-бэкенд** (`JsonStore`): файл + эталонная семантика
  ядра, атомарная перезапись, версия формата, `JsonStoreError`;
  манифест плагина `json` с `requires_contract >=2,<3`.
- **CLI** `agoralar`: `schema add-axis|add-scalar|show`, `put`, `show`,
  `query`, `plugins list`, `store show|use`; глобальные `--store`,
  `--data`, `--config`.
- **Конфиг** `agoralar.toml`: `store`, `data`, `[plugins.<name>]`;
  поиск вверх по дереву, атомарная запись, `ConfigError`.
- `Product` + `build_product(config=...)`: приоритет аргументы > конфиг >
  значения по умолчанию.
- `KERNEL_CONTRACT_VERSION` в `mu` — проверка плагинов идёт против
  контракта ядра, а не продукта.

### Changed

- Template-scaffold удалён: pipeline-интерфейсы, `Application`/`Context`,
  неиспользуемые модули `epsilon`/`zeta`; `iota` оставлен под утилиты.
- Тесты: `tau/<module>` + `tau/repo` инварианты; покрытие 96%.
