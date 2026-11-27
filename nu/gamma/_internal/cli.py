"""CLI продукта: тонкий клиент над `Product`.

Здесь нет доменной логики: разбор аргументов, вызов контрактов ядра,
печать. Типизация значений по схеме — в ядре, выбор бэкенда — в реестре.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from axislar_core.contracts import (
    ArtifactRef,
    AxisDefinition,
    AxisKind,
    CategoricalValue,
    DomainError,
    Entity,
    EntityId,
    ErrorCode,
    FacetValue,
    Filter,
    IReferenceIndex,
    MeasurableValue,
    Operator,
    Provenance,
    Query,
    QueryResult,
    ReferenceValue,
    ScalarDefinition,
    StorageQueryError,
    StorageSchema,
    SyncPolicy,
    apply_plan,
    make_artifact,
    plan_sync,
)

from nu.psi.contracts.alpha import BUILTIN_STORE, Config
from nu.psi.contracts.beta import (
    PluginRegistry,
    SyncState,
    UnknownPluginError,
    load_state,
    record_sync,
    save_state,
    state_path_for,
    sync_now,
)
from nu.psi.contracts.iota import (
    DEFAULT_CONFIG_NAME,
    ConfigError,
    available_templates,
    find_config,
    load_config,
    load_template,
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


async def build_entity(schema: StorageSchema, args: argparse.Namespace, product: Product) -> Entity:
    facets: dict[str, tuple[FacetValue, ...]] = {}
    for token in args.facet:
        name, values = parse_facet(schema, token)
        if name in facets:
            raise CliError(f"facet {name!r} is given twice")
        facets[name] = values
    scalars = dict(parse_scalar(token) for token in args.scalar)
    artifacts = await build_artifact_refs(product, args.artifact)
    return Entity(
        id=EntityId(args.id),
        kind=args.kind,
        scalars=scalars,
        facets=facets,
        artifacts=artifacts,
    )


async def build_artifact_refs(product: Product, tokens: Sequence[str]) -> dict[str, ArtifactRef]:
    """`--artifact role=hash` → ссылки; метаданные берутся из хранилища.

    Продукт не выдумывает `media_type` и `size`: он спрашивает хранилище,
    поэтому ссылка всегда согласована с байтами.
    """
    references: dict[str, ArtifactRef] = {}
    for token in tokens:
        role, separator, content_hash = token.partition("=")
        if not separator or not role or not content_hash:
            raise CliError(f"bad --artifact {token!r}, expected role=hash")
        if role in references:
            raise CliError(f"artifact role {role!r} is given twice")
        stored = await product.core.get_artifact(content_hash)
        if stored is None:
            raise CliError(f"unknown artifact {content_hash!r}")
        references[role] = ArtifactRef(
            content_hash=stored.content_hash, media_type=stored.media_type, size=stored.size
        )
    return references


def build_query(args: argparse.Namespace) -> Query:
    return Query(
        all=tuple(parse_filter(token) for token in args.filter),
        limit=args.limit,
        offset=args.offset,
    )


def _sync_options(args: argparse.Namespace, config: Config) -> dict[str, object]:
    """Настройки источника: секция конфига, аргументы CLI, затем `--option`.

    Плагин-источник сам знает свои параметры (файловое дерево, DSN, URL),
    поэтому продукт не перечисляет их: конфиг даёт базу, явные флаги её
    уточняют, `--option` перекрывает всё.
    """
    options: dict[str, object] = config.source_options_for(args.source)
    if args.root is not None:
        options["root"] = str(args.root)
    if args.source_id is not None:
        options["source_id"] = args.source_id
    if args.levels:
        options["levels"] = [part.strip() for part in args.levels.split(",") if part.strip()]
    if args.code:
        codes: dict[str, str] = {}
        for item in args.code:
            code, separator, section = item.partition("=")
            if not separator:
                raise CliError(f"bad --code {item!r}, expected CODE=section")
            codes[code] = section
        options["codes"] = codes
    options.update(parse_options(args.option))
    return options


def parse_options(tokens: Sequence[str]) -> dict[str, object]:
    """`--option key=value` → словарь параметров плагина.

    Значение разбирается как TOML-скаляр: `true`, `42`, `3.5`, `["a","b"]`
    и `"строка"` работают как в конфиге. Что не является TOML — остаётся
    строкой (`query=SELECT * FROM t` не требует кавычек).
    """
    options: dict[str, object] = {}
    for token in tokens:
        key, separator, raw = token.partition("=")
        if not separator or not key:
            raise CliError(f"bad --option {token!r}, expected key=value")
        options[key] = parse_option_value(raw)
    return options


def parse_option_value(raw: str) -> object:
    """Разобрать значение `--option` как TOML-скаляр, иначе — строка."""
    try:
        parsed = tomllib.loads(f"value = {raw}")
    except tomllib.TOMLDecodeError:
        return raw
    return parsed.get("value", raw)


def _state_file(args: argparse.Namespace) -> Path:
    return args.state_file or state_path_for(Path.cwd())


async def _sync_once(args: argparse.Namespace, product: Product, config: Config) -> int:
    """Один прогон: план, отчёт, применение и запись истории."""
    manifest = product.registry.get(args.source)
    options = _sync_options(args, config)
    source = manifest.factory(options)

    from axislar_core.contracts import IEntitySource

    if not isinstance(source, IEntitySource):
        raise CliError(f"plugin {args.source!r} is not an entity source")

    source_id = str(options.get("source_id", ""))
    scanned = [entity async for entity in source.scan()]
    storage = product.core.storage
    existing = await storage.list_entities(include_deleted=True)
    policy = SyncPolicy(source_id=source_id, complete=not args.incomplete)
    plan = plan_sync(scanned, existing, policy)

    state_path = _state_file(args)
    history = load_state(state_path)
    previous = history.get(source_id)

    counts = plan.kinds()
    summary = (
        ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items())) or "нет изменений"
    )
    print(f"source: {source.describe()}")
    print(f"[{sync_now().isoformat(timespec='seconds')}] scanned: {len(scanned)}; plan: {summary}")
    if previous is not None:
        print(f"previous: {previous.describe()}")
    for conflict in plan.conflicts:
        print(f"conflict: {conflict.reason} ({conflict.key})", file=sys.stderr)
    for warning in getattr(source, "warnings", ()):
        print(f"warning: {warning}", file=sys.stderr)

    if not args.apply:
        print("dry run: use --apply to write changes")
        return 0

    applied = await apply_plan(storage, plan)
    record = SyncState(
        source_id=source_id,
        last_run_at=sync_now(),
        scanned=len(scanned),
        counts=counts,
        conflicts=len(plan.conflicts),
    )
    save_state(state_path, record_sync(history, record))
    print(f"applied: {applied}; state: {state_path}")
    return 0


async def run_sync(args: argparse.Namespace, product: Product, config: Config) -> int:
    """Один прогон синхронизации."""
    return await _sync_once(args, product, config)


async def run_watch(args: argparse.Namespace, product: Product, config: Config) -> int:
    """Периодическая синхронизация: N прогонов (0 — бесконечно)."""
    iteration = 0
    while True:
        code = await _sync_once(args, product, config)
        if code != 0:
            return code
        iteration += 1
        if args.iterations and iteration >= args.iterations:
            return 0
        await asyncio.sleep(args.interval)


async def run_analyze(args: argparse.Namespace, product: Product, config: Config) -> int:
    """Производные артефакты: входная роль → роль, названная анализатором."""
    manifest = product.registry.get(args.analyzer)
    options: dict[str, object] = config.plugin_options_for(args.analyzer)
    options.update(parse_options(args.option))
    analyzer = manifest.factory(options)

    from nu.psi.contracts.beta import IAnalyzer

    if not isinstance(analyzer, IAnalyzer):
        raise CliError(f"plugin {args.analyzer!r} is not an analyzer")

    entities = await product.core.storage.list_entities()
    analyzed = skipped_type = up_to_date = 0
    for entity in entities:
        reference = entity.artifacts.get(args.input_role)
        if reference is None:
            continue
        if args.output_role and args.output_role in entity.artifacts and not args.force:
            up_to_date += 1
            continue

        artifact = await product.core.get_artifact(reference.content_hash)
        if artifact is None:
            print(
                f"warning: artifact {reference.content_hash!r} is missing for {entity.id}",
                file=sys.stderr,
            )
            continue
        if not analyzer.accepts(artifact.media_type):
            skipped_type += 1
            continue

        role, data, media_type = analyzer.analyze(artifact.data, artifact.media_type)
        if (
            not isinstance(role, str)
            or not role
            or not isinstance(data, bytes)
            or not isinstance(media_type, str)
            or not media_type
        ):
            raise CliError(f"analyzer {args.analyzer!r} returned an invalid result")
        if role in entity.artifacts and not args.force:
            up_to_date += 1
            continue

        derived = make_artifact(data, media_type)
        analyzed += 1
        if not args.apply:
            continue

        errors = await product.core.put_artifact(derived)
        if errors:
            print_errors(errors)
            return 2
        updated = entity.model_copy(
            update={
                "artifacts": {
                    **entity.artifacts,
                    role: ArtifactRef(
                        content_hash=derived.content_hash,
                        media_type=derived.media_type,
                        size=derived.size,
                        derived_by=Provenance(
                            plugin=args.analyzer,
                            version=analyzer.version,
                            at=sync_now(),
                        ),
                    ),
                }
            }
        )
        errors = await product.core.upsert(updated)
        if errors:
            print_errors(errors)
            return 2

    print(f"analyzer: {analyzer.describe()}")
    print(
        f"scanned: {len(entities)}; analyzed: {analyzed}; "
        f"skipped by media type: {skipped_type}; up to date: {up_to_date}"
    )
    if not args.apply:
        print("dry run: use --apply to write changes")
    else:
        print(f"applied: {analyzed}")
    return 0


async def put_artifact(args: argparse.Namespace, product: Product) -> int:
    """Положить файл в хранилище артефактов; печатает адрес (хэш)."""
    try:
        data = args.file.read_bytes()
    except OSError as error:
        raise CliError(f"cannot read {args.file}: {error}") from error
    try:
        artifact = make_artifact(data, args.media_type)
    except ValueError as error:  # pydantic ValidationError тоже ValueError
        raise CliError(f"invalid artifact: {error}") from error

    errors = await product.core.put_artifact(artifact)
    if errors:
        print_errors(errors)
        return 2
    print(f"{artifact.content_hash} {artifact.size}B (deduplicated by content)")
    return 0


async def get_artifact(args: argparse.Namespace, product: Product) -> int:
    """Отдать байты артефакта: в файл или base64 в stdout."""
    artifact = await product.core.get_artifact(args.hash)
    if artifact is None:
        print(f"not found: {args.hash}", file=sys.stderr)
        return 1
    if args.out is None:
        print(base64.b64encode(artifact.data).decode("ascii"))
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(artifact.data)
    print(f"{args.out} ({artifact.size}B, {artifact.media_type})")
    return 0


async def remove_artifact(args: argparse.Namespace, product: Product) -> int:
    """Удалить артефакт, на который никто не ссылается."""
    errors = await product.core.delete_artifact(args.hash)
    if errors:
        print_errors(errors)
        return 2
    print(f"deleted {args.hash}")
    return 0


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


def render_artifacts(references: Sequence[ArtifactRef], as_json: bool) -> str:
    if as_json:
        return json.dumps(
            [
                {
                    "content_hash": reference.content_hash,
                    "media_type": reference.media_type,
                    "size": reference.size,
                }
                for reference in references
            ],
            ensure_ascii=False,
            indent=2,
        )
    if not references:
        return "<артефактов нет>"
    lines = [
        f"{reference.content_hash} {reference.media_type} {reference.size}B"
        for reference in references
    ]
    return "\n".join(lines)


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


def serve_token(args: argparse.Namespace) -> str | None:
    """Токен HTTP: аргумент, иначе переменная окружения, иначе нет."""
    raw = args.token or os.environ.get("AGORALAR_TOKEN")
    return raw or None


def is_loopback(host: str) -> bool:
    """Слушать без токена можно только на петле."""
    return host in {"localhost", "::1", "[::1]"} or host.startswith("127.")


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
        registry = PluginRegistry(core_contract=KERNEL_CONTRACT_VERSION)
        registry.get(name)

    target = args.config or find_config() or Path.cwd() / DEFAULT_CONFIG_NAME
    updated = replace(config, store=name, data=args.store_data or config.data)
    save_config(updated, target)
    print(f"store {name} -> {target}")
    return 0


async def run_init(args: argparse.Namespace) -> int:
    """Создать конфиг и схему из шаблона (ROADMAP §1).

    Шаблон не создаёт объектов: только `agoralar.toml` и оси/скаляры в
    хранилище. Оси неизменяемы (DOMAIN A4), поэтому повторный запуск умеет
    только добавлять недостающие — существующие пропускаются с сообщением.
    """
    if args.list_templates:
        for template in available_templates():
            print(template.describe())
        return 0
    if not args.template:
        print("error: нужен --template (список: agoralar init --list)", file=sys.stderr)
        return 2

    template = load_template(args.template)
    target = args.config or Path.cwd() / DEFAULT_CONFIG_NAME
    if target.exists() and not args.force:
        print(
            f"error: {target} уже существует; --force добавит недостающие оси",
            file=sys.stderr,
        )
        return 2
    if not target.exists():
        save_config(
            Config(store=template.store, plugins=dict(template.plugins)),
            target,
        )
        print(f"config {target} created from template {template.name}")

    config = load_config(target)
    product = build_product(config=config)
    added: list[str] = []
    skipped: list[str] = []
    problems: list[DomainError] = []

    for axis in template.axes:
        errors = await product.core.add_axis(axis)
        if not errors:
            added.append(f"axis {axis.name}")
        elif all(error.code is ErrorCode.DUPLICATE_AXIS for error in errors):
            skipped.append(f"axis {axis.name}")
        else:
            problems.extend(errors)

    for scalar in template.scalars:
        errors = await product.core.add_scalar(scalar)
        if not errors:
            added.append(f"scalar {scalar.name}")
        elif all(error.code is ErrorCode.DUPLICATE_SCALAR for error in errors):
            skipped.append(f"scalar {scalar.name}")
        else:
            problems.extend(errors)

    print(f"template {template.name}: added {len(added)}, skipped {len(skipped)}")
    if skipped:
        print("  уже было (оси неизменяемы): " + ", ".join(skipped))
    if problems:
        print_errors(problems)
        return 2
    return 0


async def run(args: argparse.Namespace) -> int:
    if args.command == "init":
        return await run_init(args)

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
        entity = await build_entity(await product.core.schema(), args, product)
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

    if args.command == "visualize":
        from nu.gamma._internal.visualize import collect, render

        schema = await product.core.schema()
        entities = await product.core.storage.list_entities(include_deleted=True)
        artifacts = await product.core.storage.list_artifacts()
        report = collect(product.store, schema, entities, artifacts)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(render(report), encoding="utf-8")
        print(f"report {args.output}: {report.total} объектов, {len(report.axes)} осей")
        return 0

    if args.command == "links":
        storage = product.core.storage
        if not isinstance(storage, IReferenceIndex):
            print(
                f"error: хранилище {product.store!r} не умеет обход ссылок "
                "(нет Capability.TRAVERSE_REFERENCES)",
                file=sys.stderr,
            )
            return 2
        incoming = await storage.references_to(
            EntityId(args.id),
            args.axis or None,
            include_deleted=args.include_deleted,
        )
        if args.json:
            print(
                json.dumps(
                    [link.model_dump() for link in incoming],
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(f"# total={len(incoming)}")
            for link in incoming:
                print(f"{link.axis} <- {link.entity_id}")
        return 0

    if args.command == "store-use":
        return use_store(args, config)

    if args.command == "serve":
        from nu.psi.contracts.gamma import HttpResolver

        token = serve_token(args)
        if token is None and not is_loopback(args.host):
            raise CliError(
                f"refusing to listen on {args.host} without auth: set --token or AGORALAR_TOKEN"
            )
        resolver = HttpResolver()
        api = resolver.make_http_api(product, token=token)
        resolver.serve(api, args.host, args.port)
        return 0

    if args.command == "analyze":
        return await run_analyze(args, product, config)

    if args.command == "sync":
        return await run_sync(args, product, config)

    if args.command == "watch":
        return await run_watch(args, product, config)

    if args.command == "plugins-list":
        print(render_plugins(product, args.json))
        return 0

    if args.command == "artifact-put":
        return await put_artifact(args, product)

    if args.command == "artifact-get":
        return await get_artifact(args, product)

    if args.command == "artifact-list":
        print(render_artifacts(await product.core.list_artifacts(), args.json))
        return 0

    if args.command == "artifact-remove":
        return await remove_artifact(args, product)

    raise CliError(f"unknown command {args.command!r}")  # pragma: no cover - защита


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

    init = subparsers.add_parser("init", help="создать конфиг и схему из шаблона")
    init.add_argument("--template", help="имя шаблона (список: --list)")
    init.add_argument("--list", dest="list_templates", action="store_true", help="показать шаблоны")
    init.add_argument(
        "--force",
        action="store_true",
        help="конфиг уже есть: добавить недостающие оси вместо отказа",
    )
    init.set_defaults(command="init")

    visualize = subparsers.add_parser("visualize", help="автономный HTML-отчёт о хранилище")
    visualize.add_argument(
        "--output",
        type=Path,
        default=Path("agoralar-report.html"),
        help="куда записать отчёт (по умолчанию agoralar-report.html)",
    )
    visualize.set_defaults(command="visualize")

    links = subparsers.add_parser("links", help="кто ссылается на объект")
    links.add_argument("--id", required=True, help="id объекта")
    links.add_argument("--axis", action="append", default=[], help="ограничить осью")
    links.add_argument(
        "--include-deleted", dest="include_deleted", action="store_true", help="включая tombstone"
    )
    links.add_argument("--json", action="store_true")
    links.set_defaults(command="links")

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

    analyze = subparsers.add_parser("analyze", help="производные артефакты анализатором")
    analyze.add_argument("--analyzer", default="text", help="имя плагина-анализатора")
    analyze.add_argument("--input-role", default="original", help="роль входного артефакта")
    analyze.add_argument("--output-role", help="подсказка: готовую роль не пересчитывать")
    analyze.add_argument("--option", action="append", default=[], metavar="KEY=VALUE")
    analyze.add_argument("--force", action="store_true", help="пересчитать даже готовое")
    analyze.add_argument("--apply", action="store_true", help="записать (иначе dry run)")
    analyze.set_defaults(command="analyze")

    artifact = subparsers.add_parser("artifact", help="артефакты: байты по хэшу")
    artifact_sub = artifact.add_subparsers(dest="artifact_command", required=True)
    artifact_put = artifact_sub.add_parser("put", help="положить файл")
    artifact_put.add_argument("file", type=Path)
    artifact_put.add_argument("--media-type", dest="media_type", required=True)
    artifact_put.set_defaults(command="artifact-put")
    artifact_get = artifact_sub.add_parser("get", help="получить байты")
    artifact_get.add_argument("hash")
    artifact_get.add_argument("--out", type=Path, help="файл для байтов (иначе base64)")
    artifact_get.set_defaults(command="artifact-get")
    artifact_ls = artifact_sub.add_parser("ls", help="список артефактов")
    artifact_ls.add_argument("--json", action="store_true")
    artifact_ls.set_defaults(command="artifact-list")
    artifact_rm = artifact_sub.add_parser("rm", help="удалить неиспользуемый артефакт")
    artifact_rm.add_argument("hash")
    artifact_rm.set_defaults(command="artifact-remove")

    put = subparsers.add_parser("put", help="записать объект")
    put.add_argument("--id", required=True)
    put.add_argument("--kind", required=True, help="вид объекта: edit, note, ...")
    put.add_argument("--scalar", action="append", default=[], help="name=value")
    put.add_argument("--facet", action="append", default=[], help="axis=v1,v2")
    put.add_argument("--artifact", action="append", default=[], help="role=hash")
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

    serve = subparsers.add_parser("serve", help="HTTP-интерфейс продукта")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument(
        "--token",
        help="bearer-токен HTTP; иначе берётся из AGORALAR_TOKEN. "
        "Без токена сервер слушает только loopback",
    )
    serve.set_defaults(command="serve")

    sync = subparsers.add_parser("sync", help="синхронизировать хранилище с источником")
    sync.add_argument("--source", default="filesystem", help="имя плагина-источника")
    sync.add_argument("--root", type=Path, help="корень источника (для filesystem)")
    sync.add_argument("--source-id", dest="source_id", help="стабильный id источника")
    sync.add_argument("--levels", help="оси по уровням каталогов через запятую")
    sync.add_argument("--code", action="append", default=[], help="CODE=section для сверки")
    sync.add_argument(
        "--option",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="параметр плагина-источника (перекрывает конфиг)",
    )
    sync.add_argument("--incomplete", action="store_true", help="скан неполный: не удалять")
    sync.add_argument("--apply", action="store_true", help="применить план (иначе dry run)")
    sync.add_argument("--state-file", type=Path, help="файл истории прогонов")
    sync.set_defaults(command="sync")

    watch = subparsers.add_parser("watch", help="периодическая синхронизация")
    watch.add_argument("--source", default="filesystem")
    watch.add_argument("--root", type=Path)
    watch.add_argument("--source-id", dest="source_id")
    watch.add_argument("--levels")
    watch.add_argument("--code", action="append", default=[])
    watch.add_argument("--option", action="append", default=[], metavar="KEY=VALUE")
    watch.add_argument("--incomplete", action="store_true")
    watch.add_argument("--apply", action="store_true")
    watch.add_argument("--state-file", type=Path)
    watch.add_argument("--interval", type=float, default=60.0, help="пауза между прогонами, с")
    watch.add_argument("--iterations", type=int, default=0, help="0 — бесконечно")
    watch.set_defaults(command="watch")

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
    except (CliError, ConfigError, UnknownPluginError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except StorageQueryError as error:  # движок отказал: запрос не прошёл валидацию
        print_errors(error.errors)
        return 2
    except RuntimeError as error:  # ошибки бэкендов-плагинов (JsonStoreError и др.)
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
