"""Checksum validators for Australian business identifiers.

ABN, ACN and TFN all carry a check digit. Validating them at entry time stops
transposed/mistyped identifiers from reaching generated financial statements and
ATO lodgement figures, where they are expensive to discover and correct.

Algorithms per the ATO/ASIC published specifications.
"""
from __future__ import annotations

ABN_WEIGHTS = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
ACN_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 1)
TFN_WEIGHTS = (1, 4, 3, 7, 5, 8, 6, 9, 10)


def normalise(value: str | None) -> str:
    """Strip spaces and common separators from an identifier."""
    if not value:
        return ""
    return "".join(ch for ch in str(value) if ch.isdigit())


def is_valid_abn(value: str | None) -> bool:
    """An 11-digit ABN is valid when, after subtracting 1 from the first digit,
    the weighted sum of its digits is divisible by 89."""
    digits = normalise(value)
    if len(digits) != 11:
        return False
    nums = [int(d) for d in digits]
    nums[0] -= 1
    return sum(n * w for n, w in zip(nums, ABN_WEIGHTS)) % 89 == 0


def is_valid_acn(value: str | None) -> bool:
    """A 9-digit ACN is valid when the weighted sum of the first 8 digits gives
    a complement-of-10 remainder equal to the 9th (check) digit."""
    digits = normalise(value)
    if len(digits) != 9:
        return False
    total = sum(int(d) * w for d, w in zip(digits[:8], ACN_WEIGHTS))
    return (10 - (total % 10)) % 10 == int(digits[8])


def is_valid_tfn(value: str | None) -> bool:
    """A 9-digit TFN is valid when its weighted digit sum is divisible by 11."""
    digits = normalise(value)
    if len(digits) != 9:
        return False
    return sum(int(d) * w for d, w in zip(digits, TFN_WEIGHTS)) % 11 == 0
