# Changelog

All notable changes to AgoraLar are documented in this file.

Format based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

Продукт ответвлён от шаблона BaseLar v0.7.4; историю шаблона см. в BaseLar.

## [Unreleased]

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
