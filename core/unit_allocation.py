"""Splitting an amount across a unit register, exactly.

A unit trust's income follows the register arithmetically, which makes rounding
the only interesting part: three holders sharing $100,000 in thirds must still
total $100,000, not $99,999.99. Allocation therefore runs on integer cents and
distributes the remainder by largest fractional part, so the parts always sum
to the whole.

Kept free of the ORM so the arithmetic can be tested on its own.
"""
from decimal import Decimal, ROUND_HALF_UP

CENTS = Decimal("0.01")


def allocate_by_units(total, holdings):
    """Split ``total`` across ``holdings`` in proportion to units held.

    ``holdings`` is a list of ``(key, units)`` pairs. Returns ``{key: Decimal}``
    whose values sum exactly to ``total`` rounded to the cent.

    Raises ValueError when no units are on issue -- distributing a fixed trust's
    income with an empty register is not a rounding question, it is a
    misconfiguration, and it must not silently allocate nothing.
    """
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
