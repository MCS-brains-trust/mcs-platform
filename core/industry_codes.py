"""
ATO Business Industry Codes 2021 (NAT 1827-12.2021).

This module provides the official 5-digit industry codes used on Australian
tax returns and schedules.  The codes are grouped by ANZSIC Division for
display in ``<optgroup>`` elements within the Entity form.

Usage
-----
    from core.industry_codes import INDUSTRY_CHOICES, get_industry_label

    # Django model field
    industry = models.CharField(max_length=5, choices=INDUSTRY_CHOICES, ...)

    # Programmatic lookup
    label = get_industry_label("69320")  # "Accountant"
"""

import json
import os

# ── Load the flat code → description mapping ──────────────────────────────
_FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

with open(os.path.join(_FIXTURE_DIR, "ato_industry_codes.json")) as _f:
    INDUSTRY_CODE_MAP: dict[str, str] = json.load(_f)

# ── ANZSIC Division ranges ────────────────────────────────────────────────
_DIVISIONS = [
    ("A", "Agriculture, Forestry and Fishing", "01", "05"),
    ("B", "Mining", "06", "10"),
    ("C", "Manufacturing", "11", "25"),
    ("D", "Electricity, Gas, Water and Waste Services", "26", "29"),
    ("E", "Construction", "30", "32"),
    ("F", "Wholesale Trade", "33", "38"),
    ("G", "Retail Trade", "39", "43"),
    ("H", "Accommodation and Food Services", "44", "45"),
    ("I", "Transport, Postal and Warehousing", "46", "53"),
    ("J", "Information Media and Telecommunications", "54", "61"),
    ("K", "Financial and Insurance Services", "62", "64"),
    ("L", "Rental, Hiring and Real Estate Services", "66", "67"),
    ("M", "Professional, Scientific and Technical Services", "69", "70"),
    ("N", "Administrative and Support Services", "72", "73"),
    ("O", "Public Administration and Safety", "75", "77"),
    ("P", "Education and Training", "80", "82"),
    ("Q", "Health Care and Social Assistance", "84", "87"),
    ("R", "Arts and Recreation Services", "89", "92"),
    ("S", "Other Services", "94", "96"),
    ("T", "Private Households", "97", "97"),
    ("X", "ATO Use Only", "98", "99"),
]


def _division_for_code(code: str) -> str:
    """Return the ANZSIC Division label for a given 5-digit code."""
    prefix = code[:2]
    for _letter, name, start, end in _DIVISIONS:
        if start <= prefix <= end:
            return name
    return "Other"


# ── Build grouped choices for Django (with optgroup support) ──────────────
def _build_choices():
    """
    Return a list suitable for ``models.CharField(choices=...)``.

    Structure::

        [
            ("Division Name", [
                ("01110", "01110 – Fruit tree nursery operation (under cover)"),
                ...
            ]),
            ...
        ]
    """
    groups: dict[str, list[tuple[str, str]]] = {}
    for code in sorted(INDUSTRY_CODE_MAP):
        div = _division_for_code(code)
        label = f"{code} – {INDUSTRY_CODE_MAP[code]}"
        groups.setdefault(div, []).append((code, label))

    # Preserve ANZSIC order
    ordered = []
    seen = set()
    for _letter, name, _s, _e in _DIVISIONS:
        if name in groups and name not in seen:
            ordered.append((name, groups[name]))
            seen.add(name)
    # Catch any stragglers
    for name, items in groups.items():
        if name not in seen:
            ordered.append((name, items))
    return ordered


INDUSTRY_CHOICES = _build_choices()

# Flat choices list (without optgroups) for validation
INDUSTRY_CHOICES_FLAT = [
    (code, f"{code} – {desc}") for code, desc in sorted(INDUSTRY_CODE_MAP.items())
]


def get_industry_label(code: str) -> str:
    """Return the human-readable label for a 5-digit ATO industry code."""
    desc = INDUSTRY_CODE_MAP.get(code, "")
    return f"{code} – {desc}" if desc else code or ""


def get_industry_description(code: str) -> str:
    """Return just the description (without the code prefix)."""
    return INDUSTRY_CODE_MAP.get(code, "")


# ── Mapping from old slug-based industry values to nearest ATO code ───────
OLD_INDUSTRY_TO_ATO = {
    "accounting": "69320",
    "legal": "69310",
    "consulting": "69629",
    "it_services": "70000",
    "engineering": "69210",
    "architecture": "69210",
    "financial_services": "64190",
    "real_estate": "67200",
    "marketing": "69400",
    "professional_other": "69629",
    "medical_gp": "85110",
    "medical_specialist": "85122",
    "dental": "85310",
    "allied_health": "85391",
    "pharmacy": "42712",
    "veterinary": "69700",
    "healthcare_other": "85399",
    "construction": "30190",
    "electrical": "32310",
    "plumbing": "32320",
    "trades_other": "32410",
    "restaurant": "45110",
    "hotel": "44000",
    "catering": "45130",
    "food_manufacturing": "11990",
    "hospitality_other": "45110",
    "retail": "42799",
    "ecommerce": "43109",
    "wholesale": "38000",
    "transport": "46210",
    "courier": "51010",
    "agriculture": "01490",
    "mining": "09909",
    "fishing": "04130",
    "manufacturing": "24990",
    "nfp_charity": "95510",
    "nfp_association": "95510",
    "nfp_other": "95510",
    "education": "80100",
    "childcare": "87100",
    "property_investment": "67120",
    "investment": "64190",
    "smsf_industry": "63300",
    "beauty": "95391",
    "fitness": "91110",
    "cleaning": "73110",
    "security": "77120",
    "other": "",
}
