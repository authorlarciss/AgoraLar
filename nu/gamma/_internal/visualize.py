"""HTML-отчёт о хранилище (ROADMAP §7).

Данные считает `nu.beta.public.stats.store_stats` — здесь только рендеринг.
Файл автономный: без сети, без внешних ресурсов, без JavaScript. Все
пользовательские строки экранируются.
"""

from __future__ import annotations

import json
from html import escape
from typing import Any

from axislar_core.contracts import AxisKind

from nu.psi.contracts.beta import AxisStats, ScalarStats, StoreStats, store_stats

#: Обратная совместимость: раньше сборка и рендеринг жили в одном модуле.
collect = store_stats
Report = StoreStats

#: Максимальная ширина полосы распределения, %.
BAR_WIDTH = 100


# ----------------------------------------------------------------- рендеринг


def render(report: Report) -> str:
    """Собрать автономный HTML."""
    rows = "\n".join(_axis_row(axis) for axis in report.axes) or _empty_row()
    scalars = "\n".join(_scalar_row(scalar) for scalar in report.scalars)
    warnings = _warnings(report)
    return _TEMPLATE.format(
        store=escape(report.store),
        total=report.total,
        visible=report.visible,
        deleted=report.deleted,
        artifacts=report.artifacts,
        artifact_bytes=_human_bytes(report.artifact_bytes),
        rows=rows,
        scalars=scalars,
        warnings=warnings,
        payload=escape(json.dumps(report.as_dict(), ensure_ascii=False, indent=2)),
    )


def _payload(report: Report) -> dict[str, Any]:
    """Данные отчёта как JSON — чтобы его можно было сравнить или распарсить."""
    return {
        "store": report.store,
        "objects": {"total": report.total, "visible": report.visible, "deleted": report.deleted},
        "artifacts": {"count": report.artifacts, "bytes": report.artifact_bytes},
        "axes": [
            {
                "name": axis.name,
                "kind": axis.kind,
                "multi": axis.multi,
                "filled": axis.filled,
                "share": round(axis.share, 1),
                "distinct": axis.distinct,
                "top": list(axis.top),
                "min": axis.minimum,
                "median": axis.middle,
                "max": axis.maximum,
            }
            for axis in report.axes
        ],
        "scalars": [
            {"name": scalar.name, "filled": scalar.filled, "share": round(scalar.share, 1)}
            for scalar in report.scalars
        ],
    }


def _axis_row(axis: AxisStats) -> str:
    details = _axis_details(axis)
    return (
        "<tr>"
        f"<td><code>{escape(axis.name)}</code></td>"
        f"<td>{escape(axis.kind)}{' · multi' if axis.multi else ''}</td>"
        f'<td class="num">{axis.filled} / {axis.total}</td>'
        f'<td><span class="bar" style="width:{axis.share:.1f}%"></span>'
        f'<span class="pct">{axis.share:.0f}%</span></td>'
        f'<td class="num">{axis.distinct}</td>'
        f"<td>{details}</td>"
        "</tr>"
    )


def _axis_details(axis: AxisStats) -> str:
    if axis.kind == AxisKind.MEASURABLE.value:
        if axis.minimum is None:
            return '<span class="muted">нет значений</span>'
        return (
            f"min {_number(axis.minimum)} · медиана {_number(axis.middle)}"
            f" · max {_number(axis.maximum)}"
        )
    if not axis.top:
        return '<span class="muted">нет значений</span>'
    peak = axis.top[0][1] or 1
    items = "".join(
        f'<li><span class="value">{escape(value)}</span>'
        f'<span class="bar small" style="width:{count / peak * BAR_WIDTH:.1f}%"></span>'
        f'<span class="count">{count}</span></li>'
        for value, count in axis.top
    )
    return f'<ul class="dist">{items}</ul>'


def _scalar_row(scalar: ScalarStats) -> str:
    return (
        "<tr>"
        f"<td><code>{escape(scalar.name)}</code></td>"
        f'<td class="num">{scalar.filled} / {scalar.total}</td>'
        f'<td><span class="bar" style="width:{scalar.share:.1f}%"></span>'
        f'<span class="pct">{scalar.share:.0f}%</span></td>'
        "</tr>"
    )


def _empty_row() -> str:
    return '<tr><td colspan="6" class="muted">в схеме нет осей</td></tr>'


def _warnings(report: Report) -> str:
    if not report.empty_axes:
        return ""
    names = ", ".join(escape(name) for name in report.empty_axes)
    return (
        '<p class="note"><strong>Пустые оси:</strong> '
        f"{names} — значений нет ни у одного объекта. "
        "Ось неизменяема (DOMAIN A4), поэтому это либо забытый импорт, "
        "либо схема, опередившая данные.</p>"
    )


def _number(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:g}"


def _human_bytes(size: int) -> str:
    units = ("Б", "КБ", "МБ", "ГБ", "ТБ")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} Б"  # pragma: no cover - недостижимо


_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>AgoraLar: отчёт о хранилище</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 14px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 60rem;
         padding: 0 1rem; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 .25rem; }}
  h2 {{ font-size: 1.05rem; margin: 2rem 0 .5rem; }}
  .muted {{ opacity: .6; }}
  .cards {{ display: flex; flex-wrap: wrap; gap: .75rem; margin: 1rem 0; }}
  .card {{ border: 1px solid color-mix(in srgb, currentColor 20%, transparent);
           border-radius: .5rem; padding: .5rem .75rem; min-width: 9rem; }}
  .card b {{ display: block; font-size: 1.25rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: .4rem .5rem; vertical-align: top;
            border-bottom: 1px solid color-mix(in srgb, currentColor 12%, transparent); }}
  th {{ font-weight: 600; font-size: .85rem; opacity: .75; }}
  code {{ font: 13px ui-monospace, monospace; }}
  .num {{ text-align: right; white-space: nowrap; }}
  .bar {{ display: inline-block; height: .6rem; background: currentColor; opacity: .35;
          border-radius: .3rem; vertical-align: middle; }}
  .bar.small {{ height: .4rem; margin: 0 .4rem; }}
  .pct {{ margin-left: .4rem; font-size: .85rem; opacity: .7; }}
  ul.dist {{ list-style: none; margin: 0; padding: 0; }}
  ul.dist li {{ display: flex; align-items: center; gap: .25rem; }}
  ul.dist .value {{ min-width: 8rem; }}
  ul.dist .count {{ font-size: .85rem; opacity: .7; }}
  .note {{ border-left: 3px solid currentColor; padding: .5rem .75rem; opacity: .85; }}
  pre {{ overflow-x: auto; padding: .75rem; border-radius: .5rem;
         background: color-mix(in srgb, currentColor 6%, transparent); }}
  footer {{ margin-top: 2rem; font-size: .8rem; opacity: .6; }}
</style>
</head>
<body>
<h1>AgoraLar: отчёт о хранилище</h1>
<p class="muted">store: <code>{store}</code></p>

<div class="cards">
  <div class="card"><span class="muted">объектов</span><b>{total}</b></div>
  <div class="card"><span class="muted">видимых</span><b>{visible}</b></div>
  <div class="card"><span class="muted">tombstone</span><b>{deleted}</b></div>
  <div class="card"><span class="muted">артефактов</span><b>{artifacts}</b></div>
  <div class="card"><span class="muted">байт артефактов</span><b>{artifact_bytes}</b></div>
</div>

{warnings}

<h2>Оси</h2>
<table>
  <thead>
    <tr><th>ось</th><th>тип</th><th class="num">заполнено</th><th>доля</th>
        <th class="num">значений</th><th>распределение</th></tr>
  </thead>
  <tbody>
{rows}
  </tbody>
</table>

<h2>Скаляры</h2>
<table>
  <thead><tr><th>скаляр</th><th class="num">заполнено</th><th>доля</th></tr></thead>
  <tbody>
{scalars}
  </tbody>
</table>

<h2>Данные отчёта</h2>
<pre>{payload}</pre>

<footer>Отчёт автономный: без сети, без внешних ресурсов.
Сгенерирован AgoraLar (`agoralar visualize`).</footer>
</body>
</html>
"""


__all__ = ["AxisStats", "Report", "ScalarStats", "collect", "render"]
