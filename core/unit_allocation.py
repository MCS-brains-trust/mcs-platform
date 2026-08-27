"""Splitting an amount across a unit register, exactly.

A unit trust's income follows the register arithmetically, which makes rounding
the only interesting part: three holders sharing $100,000 in thirds must still
total $100,000, not $99,999.99. Allocation therefore runs on integer cents and
distributes the remainder by largest fractional part, so the parts always sum
to the whole.

Kept free of the ORM so the arithmetic can be tested on its own.

Same idea (Hare / largest-remainder), different scale: EntityOfficer.
recalculate_unit_percentages (core/models.py) distributes a fixed 100.00%
across hundredths-of-a-percent, fused with per-holder save() calls and audit
history writes; this module distributes an arbitrary money amount across
cents, with no ORM. If a third caller of this algorithm appears, the shared
extraction should be the pure integer form
``largest_remainder(target: int, weights: list[int]) -> list[int]`` rather
than routing percentages through a money round-trip.

Caller note: a ``None`` in a holding's unit count is not validated here --
``sum()`` over ``holdings`` raises a bare ``TypeError``, not this module's
own ``ValueError``. Callers building ``holdings`` from EntityOfficer rows
should filter or coerce ``units_held`` before calling in.
"""
from decimal import Decimal, ROUND_HALF_UP

CENTS = Decimal("0.01")


def allocate_by_units(total, holdings):
    """Split ``total`` across ``holdings`` in proportion to units held.

    ``holdings`` is an iterable of ``(key, units)`` pairs (each key must be
    unique -- see below). Returns ``{key: Decimal}`` whose values sum exactly
    to ``total`` rounded to the cent.

    Raises ValueError when no units are on issue -- distributing a fixed trust's
    income with an empty register is not a rounding question, it is a
    misconfiguration, and it must not silently allocate nothing. Raises
    ValueError on a repeated key for the same reason: silently accumulating
    two rows under one key would hide a caller bug (e.g. a duplicated
    register row) behind arithmetic that still happens to tie out.

    A negative ``total`` (a loss) is allocated by stripping the sign,
    allocating on the absolute value, then re-applying the sign to every
    result. This is NOT what prevents negative-floor-division bugs --
    Python's ``divmod`` remainder is already non-negative whenever the
    divisor (``total_units``) is positive, sign strip or not. What the sign
    strip actually buys is fairness: a profit split and a loss split of the
    same register land the odd cent on the same holder, rather than the
    absolute-value symmetry breaking depending on the sign of ``total``.
    """
    holdings = list(holdings)
    if not holdings:
        raise ValueError("cannot allocate across an empty unit register")

    total_units = sum(units for _, units in holdings)
    if total_units <= 0:
        raise ValueError("cannot allocate with no units on issue")

    cents = int(
        (Decimal(total) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    sign = -1 if cents < 0 else 1
    cents = abs(cents)

    whole_parts = {}
    remainders = []
    allocated = 0
    for key, units in holdings:
        if key in whole_parts:
            raise ValueError(f"duplicate key in unit register: {key!r}")
        exact = cents * units
        whole, remainder = divmod(exact, total_units)
        whole_parts[key] = whole
        remainders.append((remainder, key))
        allocated += whole

    # Largest remainder takes the leftover cents, one each. Tie-broken on
    # str(key) so that when two holders land on the exact same remainder,
    # which cent goes where is deterministic rather than dependent on
    # dict/list ordering -- the total is correct either way, but a stable
    # result matters for reproducibility (e.g. re-running an allocation
    # must not silently move a cent between two holders).
    leftover = cents - allocated
    ordered = sorted(remainders, key=lambda pair: (-pair[0], str(pair[1])))
    for _, key in ordered[:leftover]:
        whole_parts[key] += 1

    return {
        key: (Decimal(sign * value) / 100).quantize(CENTS)
        for key, value in whole_parts.items()
    }
