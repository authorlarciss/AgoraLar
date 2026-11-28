#!/usr/bin/env python
"""Замер раскрытия графа: `subgraph` на большом хранилище (4.1).

Критерий плана — «раскрытие узла < 300 мс на 5k объектов». Скрипт строит
синтетическое хранилище (хаб + ссылки от каждого сотого) и меряет медиану.

    python scripts/bench_graph.py --backend sqlite --n 5000
"""

from __future__ import annotations

import argparse
import asyncio
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
    StorageSchema,
)

from nu.psi.contracts.beta import subgraph

SCHEMA = StorageSchema(
    axes=(
        AxisDefinition(name="theme", kind=AxisKind.CATEGORICAL, multi=True),
        AxisDefinition(name="version", kind=AxisKind.MEASURABLE),
        AxisDefinition(name="links", kind=AxisKind.REFERENCE, multi=True, target="note"),
    )
)
THEMES = ("noir", "cyberpunk", "epic", "fantasy", "doc")
REPEATS = 5


def make_entity(index: int, total: int) -> Entity:
    facets: dict[str, Any] = {
        "theme": ({"kind": "categorical", "value": THEMES[index % len(THEMES)]},),
        "version": ({"kind": "measurable", "value": float(1 + index % 5)},),
    }
    if index % 100 == 1:
        facets["links"] = ({"kind": "reference", "target": f"e-{0:07d}"},)
    return Entity.model_validate({"id": f"e-{index:07d}", "kind": "note", "facets": facets})


def open_backend(backend: str, path: Path) -> Any:
    if backend == "sqlite":
        from agora_store_sqlite import SqliteStore

        return SqliteStore(path / "graph.sqlite3", SCHEMA)
    if backend == "json":
        from agora_store_json import JsonStore

        return JsonStore(path / "graph.json", SCHEMA)
    raise SystemExit(f"unknown backend {backend!r}")


async def median_ms(factory: Any) -> float:
    samples: list[float] = []
    for _ in range(REPEATS):
        started = time.perf_counter()
        await factory()
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.median(samples)


async def run(backend: str, n: int) -> None:
    workdir = Path(tempfile.mkdtemp(prefix=f"bench-graph-{backend}-"))
    engine = open_backend(backend, workdir)
    started = time.perf_counter()
    for index in range(n):
        errors = await engine.upsert(make_entity(index, n))
        if errors:
            raise SystemExit(f"upsert failed: {errors[0]}")
    print(f"записано {n} объектов за {(time.perf_counter() - started):.1f} с")

    hub = EntityId(f"e-{0:07d}")
    depth1 = await median_ms(lambda: subgraph(engine, [hub], depth=1))
    depth2 = await median_ms(lambda: subgraph(engine, [hub], depth=2))
    leaf = EntityId(f"e-{1:07d}")
    leaf1 = await median_ms(lambda: subgraph(engine, [leaf], depth=1, direction="both"))
    print(f"{backend}, {n} объектов:")
    print(f"  раскрытие хаба depth=1: {depth1:.0f} мс (узлов ~{n // 100 + 1})")
    print(f"  раскрытие хаба depth=2: {depth2:.0f} мс")
    print(f"  узел-лист depth=1 both: {leaf1:.0f} мс")
    print(f"  критерий 300 мс: {'да' if max(depth1, depth2, leaf1) < 300 else 'НЕТ'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Замер subgraph на синтетике")
    parser.add_argument("--backend", default="sqlite", choices=["sqlite", "json"])
    parser.add_argument("--n", type=int, default=5000)
    args = parser.parse_args()
    asyncio.run(run(args.backend, args.n))


if __name__ == "__main__":
    main()
