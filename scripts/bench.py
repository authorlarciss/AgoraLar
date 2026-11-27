#!/usr/bin/env python
"""Бенчмарк сторов: запись, чтение по id, запрос по оси, обход ссылок.

Запуск (из корня репозитория, в venv продукта):

    python scripts/bench.py --backend sqlite --n 1000
    python scripts/bench.py --backend json --n 1000 --updates 50

Скрипт намеренно простой: без зависимостей, одна машина, одна схема.
Числа нужны не для сравнения с другими продуктами, а для сравнения
версий этого репозитория между собой (до/после изменений в сторе).
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from axislar_core.contracts import (
    AxisDefinition,
    AxisKind,
    Entity,
    EntityId,
    Filter,
    IStorageEngine,
    Operator,
    Query,
    StorageSchema,
)

SCHEMA = StorageSchema(
    axes=(
        AxisDefinition(name="theme", kind=AxisKind.CATEGORICAL, multi=True),
        AxisDefinition(name="year", kind=AxisKind.MEASURABLE),
        AxisDefinition(name="links", kind=AxisKind.REFERENCE, multi=True, target="note"),
    )
)

THEMES = ("noir", "cyberpunk", "epic", "fantasy", "doc")

#: Запрос по одной оси — типичный фильтр в UI.
QUERY_ALL = Query(all=(Filter(axis="theme", op=Operator.CONTAINS, values=("noir",)),))

#: Сколько раз повторять замер запроса/обхода (берём медиану).
REPEATS = 5


def make_entity(index: int, total: int) -> Entity:
    """Объект с фасетами; каждый сотый ссылается на «хаб» (id=0)."""
    facets: dict[str, Any] = {
        "theme": ({"kind": "categorical", "value": THEMES[index % len(THEMES)]},),
        "year": ({"kind": "measurable", "value": 1990.0 + index % 30},),
    }
    if index % 100 == 1 and index != 0:
        facets["links"] = ({"kind": "reference", "target": f"e-{0:07d}"},)
    return Entity.model_validate({"id": f"e-{index:07d}", "kind": "note", "facets": facets})


def open_backend(backend: str, path: Path) -> IStorageEngine:
    if backend == "sqlite":
        from agora_store_sqlite import SqliteStore

        return SqliteStore(path / "bench.sqlite3", SCHEMA)
    if backend == "json":
        from agora_store_json import JsonStore

        return JsonStore(path / "bench.json", SCHEMA)
    raise SystemExit(f"unknown backend {backend!r}")


async def medians(coro_factory: Any, repeats: int = REPEATS) -> float:
    """Медиана длительности вызова в миллисекундах."""
    samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        await coro_factory()
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.median(samples)


async def measure(backend: str, n: int, updates: int) -> dict[str, float]:
    workdir = Path(tempfile.mkdtemp(prefix=f"bench-{backend}-"))
    try:
        engine = open_backend(backend, workdir)

        started = time.perf_counter()
        for index in range(n):
            errors = await engine.upsert(make_entity(index, n))
            if errors:
                raise SystemExit(f"upsert failed: {errors[0]}")
        write_ms = (time.perf_counter() - started) * 1000

        # Обновления существующих объектов — самый частый путь в UI.
        started = time.perf_counter()
        for index in range(min(updates, n)):
            await engine.upsert(make_entity(index, n))
        update_ms = (time.perf_counter() - started) * 1000

        get_ms = await medians(lambda: engine.get(EntityId(f"e-{n // 2:07d}")))
        query_ms = await medians(lambda: engine.query(QUERY_ALL))
        refs_ms = await medians(lambda: engine.references_to(EntityId(f"e-{0:07d}")))

        # Переоткрытие = полная загрузка файла/таблицы в память.
        started = time.perf_counter()
        reopened = open_backend(backend, workdir)
        await reopened.count()
        load_ms = (time.perf_counter() - started) * 1000

        links = await reopened.references_to(EntityId(f"e-{0:07d}"))
        return {
            "objects": float(n),
            "upsert_total_ms": write_ms,
            "upsert_avg_ms": write_ms / n,
            "update_avg_ms": update_ms / max(1, min(updates, n)),
            "get_ms": get_ms,
            "query_ms": query_ms,
            "references_ms": refs_ms,
            "load_ms": load_ms,
            "incoming_links": float(len(links)),
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Бенчмарк сторов AgoraLar")
    parser.add_argument("--backend", choices=("json", "sqlite"), default="sqlite")
    parser.add_argument("--n", type=int, default=1000, help="сколько объектов записать")
    parser.add_argument("--updates", type=int, default=50, help="сколько объектов обновить")
    args = parser.parse_args()

    stats = asyncio.run(measure(args.backend, args.n, args.updates))
    width = max(len(key) for key in stats)
    print(f"backend={args.backend} n={args.n} updates={args.updates}")
    for key, value in stats.items():
        print(f"  {key:<{width}} = {value:9.2f}")


if __name__ == "__main__":
    main()
