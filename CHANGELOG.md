# Changelog

All notable changes to AgoraLar are documented in this file.

Format based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

Продукт ответвлён от шаблона BaseLar v0.7.4; историю шаблона см. в BaseLar.

## [Unreleased]

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
