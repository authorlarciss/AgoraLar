"""Разбор пользовательского ввода в DTO ядра (`axis:op=v1,v2`).

Живёт в `iota`, потому что это общая утилита: одинаковый синтаксис
используют CLI (`--filter`) и HTTP (`/api/v1/objects?filter=...`). Ошибки —
`ParseError`, чтобы вызывающая сторона отображала их в свой транспорт
(код выхода или HTTP 400).
"""

from __future__ import annotations

from axislar_core.contracts import Filter, Operator


class ParseError(ValueError):
    """Строка запроса не разбирается."""


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


def parse_filter(token: str) -> Filter:
    """`axis:op=v1,v2` → `Filter`."""
    head, separator, raw = token.partition("=")
    axis, colon, op_name = head.partition(":")
    if not colon or not axis or not op_name:
        raise ParseError(f"bad filter {token!r}, expected axis:op=values")

    try:
        op = Operator(op_name)
    except ValueError as error:
        known = ", ".join(item.value for item in Operator)
        raise ParseError(f"unknown operator {op_name!r}; known: {known}") from error

    if op in NULLARY_OPERATORS:
        return Filter(axis=axis, op=op)
    if not separator:
        raise ParseError(f"filter {token!r} requires values")

    raw_values = [piece for piece in raw.split(",") if piece]
    if not raw_values:
        raise ParseError(f"filter {token!r} requires at least one value")

    if op in NUMERIC_OPERATORS:
        try:
            numeric: tuple[str | float, ...] = tuple(float(piece) for piece in raw_values)
        except ValueError as error:
            raise ParseError(f"operator {op.value!r} requires numbers") from error
        return Filter(axis=axis, op=op, values=numeric)
    return Filter(axis=axis, op=op, values=tuple(raw_values))


__all__ = ["NULLARY_OPERATORS", "NUMERIC_OPERATORS", "ParseError", "parse_filter"]
