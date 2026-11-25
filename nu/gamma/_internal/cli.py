"""CLI продукта: тонкий клиент над `Product`.

Здесь нет доменной логики: разбор аргументов, вызов контрактов ядра,
печать. Типизация значений по схеме — в ядре, выбор бэкенда — в реестре.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from axislar_core.contracts import (
    AxisDefinition,
    AxisKind,
    CategoricalValue,
    DomainError,
    Entity,
    EntityId,
    FacetValue,
    Filter,
    MeasurableValue,
    Operator,
    Query,
    QueryResult,
    ReferenceValue,
    ScalarDefinition,
    StorageSchema,
)

from nu.psi.contracts.alpha import BUILTIN_STORE, Config
from nu.psi.contracts.beta import PluginRegistry, UnknownPluginError
from nu.psi.contracts.delta import JsonStoreError, builtin_plugins
from nu.psi.contracts.iota import (
    DEFAULT_CONFIG_NAME,
    ConfigError,
    find_config,
    load_config,
    save_config,
)
from nu.psi.contracts.mu import KERNEL_CONTRACT_VERSION
from nu.psi.product import Product, build_product

#: Числовые операторы: значения разбираются как float.
NUMERIC_OPERATORS = frozenset(
    {
        Operator.EQ,
        Operator.GT,
        Operator.LT,
        Operator.GTE,
        Operator.LTE,
        Operator.BETWEEN,
    }
)

#: Операторы без значений.
NULLARY_OPERATORS = frozenset({Operator.EXISTS, Operator.NOT_EXISTS})


class CliError(ValueError):
    """Ошибка ввода пользователя: печатается без трейсбека."""


# ------------------------------------------------------------------ разбор


def parse_filter(token: str) -> Filter:
    """`axis:op=v1,v2` → Filter."""
    head, separator, raw = token.partition("=")
    axis, colon, op_name = head.partition(":")
    if not colon or not axis or not op_name:
        raise CliError(f"bad filter {token!r}, expected axis:op=values")

    try:
        op = Operator(op_name)
    except ValueError as error:
        known = ", ".join(item.value for item in Operator)
        raise CliError(f"unknown operator {op_name!r}; known: {known}") from error

    if op in NULLARY_OPERATORS:
        return Filter(axis=axis, op=op)
    if not separator:
        raise CliError(f"filter {token!r} requires values")

    raw_values = [piece for piece in raw.split(",") if piece]
    if not raw_values:
        raise CliError(f"filter {token!r} requires at least one value")

    if op in NUMERIC_OPERATORS:
        try:
            numeric: tuple[str | float, ...] = tuple(float(piece) for piece in raw_values)
        except ValueError as error:
            raise CliError(f"operator {op.value!r} requires numbers") from error
        return Filter(axis=axis, op=op, values=numeric)
    return Filter(axis=axis, op=op, values=tuple(raw_values))


def parse_scalar(token: str) -> tuple[str, str]:
    """`title=Alpha` → (name, value)."""
    name, separator, value = token.partition("=")
    if not separator or not name:
        raise CliError(f"bad scalar {token!r}, expected name=value")
    return name, value


def parse_facet(schema: StorageSchema, token: str) -> tuple[str, tuple[FacetValue, ...]]:
    """`theme=cyberpunk,noir` → фасет с типом из схемы."""
    name, separator, raw = token.partition("=")
    if not separator or not name:
        raise CliError(f"bad facet {token!r}, expected axis=value[,value]")

    axis = schema.axis(name)
    if axis is None:
        known = ", ".join(item.name for item in schema.axes) or "<нет осей>"
        raise CliError(f"unknown axis {name!r}; declared: {known}")

    raw_values = [piece for piece in raw.split(",") if piece]
    if not raw_values:
        raise CliError(f"facet {token!r} requires at least one value")

    values: list[FacetValue] = []
    for piece in raw_values:
        if axis.kind is AxisKind.MEASURABLE:
            try:
                values.append(MeasurableValue(value=float(piece)))
            except ValueError as error:
                raise CliError(f"axis {name!r} is measurable, got {piece!r}") from error
        elif axis.kind is AxisKind.REFERENCE:
            values.append(ReferenceValue(target=EntityId(piece)))
        else:
            values.append(CategoricalValue(value=piece))
    return name, tuple(values)


def build_entity(schema: StorageSchema, args: argparse.Namespace) -> Entity:
    facets: dict[str, tuple[FacetValue, ...]] = {}
    for token in args.facet:
        name, values = parse_facet(schema, token)
        if name in facets:
            raise CliError(f"facet {name!r} is given twice")
        facets[name] = values
    scalars = dict(parse_scalar(token) for token in args.scalar)
    return Entity(id=EntityId(args.id), kind=args.kind, scalars=scalars, facets=facets)


def build_query(args: argparse.Namespace) -> Query:
    return Query(
        all=tuple(parse_filter(token) for token in args.filter),
        limit=args.limit,
        offset=args.offset,
    )


# ------------------------------------------------------------------- печать


def print_errors(errors: Sequence[DomainError]) -> None:
    for error in errors:
        print(str(error), file=sys.stderr)


def render_schema(schema: StorageSchema, as_json: bool) -> str:
    if as_json:
        return schema.model_dump_json(indent=2)
    if not schema.axes and not schema.scalars:
        return "<пустая схема>"
    lines = [
        f"axis {axis.name}: {axis.kind.value}"
        f"{' multi' if axis.multi else ''}"
        f"{f' target={axis.target}' if axis.target else ''}"
        f"{f' strict={list(axis.values)}' if axis.strict else ''}"
        for axis in schema.axes
    ]
    lines += [
        f"scalar {scalar.name}{' searchable' if scalar.searchable else ''}"
        for scalar in schema.scalars
    ]
    return "\n".join(lines)


def render_result(result: QueryResult, as_json: bool) -> str:
    if as_json:
        return result.model_dump_json(indent=2)
    ids = "\n".join(str(entity_id) for entity_id in result.ids)
    header = f"# total={result.total} limit={result.limit} offset={result.offset}"
    return f"{header}\n{ids}".rstrip()


def render_plugins(product: Product, as_json: bool) -> str:
    available = [manifest.describe() for manifest in product.registry.available]
    rejected = [item.describe() for item in product.registry.rejected]
    if as_json:
        return json.dumps(
            {
                "available": [
                    {
                        "name": manifest.name,
                        "kind": manifest.kind.value,
                        "requires_contract": manifest.requires_contract,
                    }
                    for manifest in product.registry.available
                ],
                "rejected": [
                    {"name": item.name, "kind": item.kind, "reason": item.reason}
                    for item in product.registry.rejected
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    lines = [f"available: {', '.join(available) or '<нет>'}"]
    lines += [f"rejected: {item}" for item in rejected]
    return "\n".join(lines)


# ------------------------------------------------------------------ команды


def resolve_config(args: argparse.Namespace) -> Config:
    """Прочитать конфиг: явный `--config` или поиск вверх от cwd."""
    return load_config(args.config)


def render_config_state(config: Config, path: Path | None) -> str:
    """Человекочитаемое состояние конфига."""
    source = str(path or find_config() or "<нет файла: значения по умолчанию>")
    lines = [f"config: {source}", f"store: {config.store}"]
    if config.data is not None:
        lines.append(f"data: {config.data}")
    for name, section in sorted(config.plugins.items()):
        pairs = ", ".join(f"{key}={value}" for key, value in sorted(section.items()))
        lines.append(f"plugins.{name}: {pairs or '<пусто>'}")
    return "\n".join(lines)


def use_store(args: argparse.Namespace, config: Config) -> int:
    """Записать выбранное хранилище в конфиг, проверив его в реестре."""
    name: str = args.name
    if name != BUILTIN_STORE:
        registry = PluginRegistry(core_contract=KERNEL_CONTRACT_VERSION, builtins=builtin_plugins())
        registry.get(name)

    target = args.config or find_config() or Path.cwd() / DEFAULT_CONFIG_NAME
    updated = replace(config, store=name, data=args.store_data or config.data)
    save_config(updated, target)
    print(f"store {name} -> {target}")
    return 0


async def run(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    store = args.store or config.store
    options = {"path": str(args.data)} if args.data is not None else {}
    product = build_product(store, options, config=config)

    if args.command == "config-show":
        print(render_config_state(config, args.config))
        return 0

    if args.command == "store-use":
        return use_store(args, config)

    if args.command == "schema-show":
        print(render_schema(await product.core.schema(), args.json))
        return 0

    if args.command == "schema-add-axis":
        axis = AxisDefinition(
            name=args.name,
            kind=args.kind,
            multi=args.multi,
            target=args.target,
            strict=args.strict,
            values=tuple(args.value),
        )
        errors = await product.core.add_axis(axis)
        if errors:
            print_errors(errors)
            return 2
        print(f"axis {axis.name} added")
        return 0

    if args.command == "schema-add-scalar":
        scalar = ScalarDefinition(name=args.name, searchable=args.searchable)
        errors = await product.core.add_scalar(scalar)
        if errors:
            print_errors(errors)
            return 2
        print(f"scalar {scalar.name} added")
        return 0

    if args.command == "put":
        entity = build_entity(await product.core.schema(), args)
        errors = await product.core.upsert(entity)
        if errors:
            print_errors(errors)
            return 2
        print(f"stored {entity.id}")
        return 0

    if args.command == "show":
        found = await product.core.get(EntityId(args.id))
        if found is None:
            print(f"not found: {args.id}", file=sys.stderr)
            return 1
        print(found.model_dump_json(indent=2) if args.json else found.model_dump_json())
        return 0

    if args.command == "query":
        print(render_result(await product.core.query(build_query(args)), args.json))
        return 0

    if args.command == "store-use":
        return use_store(args, config)

    if args.command == "plugins-list":
        print(render_plugins(product, args.json))
        return 0

    raise CliError(f"unknown command {args.command!r}")  # pragma: no cover


# ------------------------------------------------------------------ парсер


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agoralar", description="AgoraLar — оболочка AxisLar")
    parser.add_argument(
        "--store",
        default=None,
        help="бэкенд хранилища: memory (ядро) или плагин из реестра (json); "
        "по умолчанию — из конфига",
    )
    parser.add_argument("--data", type=Path, help="файл данных для файловых бэкендов")
    parser.add_argument("--config", type=Path, help="путь к agoralar.toml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    schema = subparsers.add_parser("schema", help="работа со схемой")
    schema_sub = schema.add_subparsers(dest="schema_command", required=True)
    show = schema_sub.add_parser("show", help="показать схему")
    show.add_argument("--json", action="store_true")
    show.set_defaults(command="schema-show")

    add_axis = schema_sub.add_parser("add-axis", help="добавить ось")
    add_axis.add_argument("--name", required=True)
    add_axis.add_argument("--kind", required=True, choices=[item.value for item in AxisKind])
    add_axis.add_argument("--multi", action="store_true")
    add_axis.add_argument("--target")
    add_axis.add_argument("--strict", action="store_true")
    add_axis.add_argument("--value", action="append", default=[], help="значение словаря")
    add_axis.set_defaults(command="schema-add-axis")

    add_scalar = schema_sub.add_parser("add-scalar", help="добавить скаляр")
    add_scalar.add_argument("--name", required=True)
    add_scalar.add_argument("--searchable", action="store_true")
    add_scalar.set_defaults(command="schema-add-scalar")

    put = subparsers.add_parser("put", help="записать объект")
    put.add_argument("--id", required=True)
    put.add_argument("--kind", required=True, help="вид объекта: edit, note, ...")
    put.add_argument("--scalar", action="append", default=[], help="name=value")
    put.add_argument("--facet", action="append", default=[], help="axis=v1,v2")
    put.set_defaults(command="put")

    show_entity = subparsers.add_parser("show", help="показать объект")
    show_entity.add_argument("--id", required=True)
    show_entity.add_argument("--json", action="store_true")
    show_entity.set_defaults(command="show")

    query = subparsers.add_parser("query", help="выполнить запрос")
    query.add_argument("--filter", action="append", default=[], help="axis:op=v1,v2")
    query.add_argument("--limit", type=int)
    query.add_argument("--offset", type=int, default=0)
    query.add_argument("--json", action="store_true")
    query.set_defaults(command="query")

    store = subparsers.add_parser("store", help="активное хранилище")
    store_sub = store.add_subparsers(dest="store_command", required=True)
    store_show = store_sub.add_parser("show", help="показать конфиг")
    store_show.set_defaults(command="config-show")
    store_use = store_sub.add_parser("use", help="выбрать хранилище и записать конфиг")
    store_use.add_argument("name")
    store_use.add_argument("--data", type=Path, dest="store_data")
    store_use.set_defaults(command="store-use")

    plugins = subparsers.add_parser("plugins", help="плагины")
    plugins_sub = plugins.add_subparsers(dest="plugins_command", required=True)
    plugins_list = plugins_sub.add_parser("list", help="список плагинов")
    plugins_list.add_argument("--json", action="store_true")
    plugins_list.set_defaults(command="plugins-list")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа CLI. Возвращает код выхода."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return asyncio.run(run(args))
    except (CliError, ConfigError, JsonStoreError, UnknownPluginError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except ValueError as error:  # DTO-валидация ввода
        print(f"error: {error}", file=sys.stderr)
        return 2


__all__ = [
    "build_parser",
    "main",
    "parse_facet",
    "parse_filter",
    "parse_scalar",
    "render_plugins",
    "render_result",
    "render_schema",
]
