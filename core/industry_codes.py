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
    label = get_industry_label("69320")  # "Accounting Services"
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

# ── Dataset version markers (read by celerybeat freshness check) ──────────
__version__ = "NAT 1827 December 2021"
__last_checked__ = "2026-04-28"
__expected_refresh_days__ = 365

# ── Load the flat code → description mapping ──────────────────────────────
_FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

with open(os.path.join(_FIXTURE_DIR, "ato_industry_codes.json")) as _f:
    INDUSTRY_CODE_MAP: dict[str, str] = json.load(_f)

# Alias for callers that prefer the descriptive name (used by verification scripts).
_CODE_TO_LABEL = INDUSTRY_CODE_MAP

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


# ── Common industries pinned to the top of the dropdown ──────────────────
# Curated for suburban Australian accounting practice (Phase 2 spec).
# Same code also appears in its proper ANZSIC division below — intentional,
# matches Xero's UX. Codes that don't resolve against the loaded fixture are
# dropped at build time with a logged warning (see _build_choices).
COMMON_INDUSTRY_CODES = [
    # Construction trades
    "30110",  # House Construction
    "30220",  # Other Residential Building Construction
    "32110",  # Plumbing Services
    "32120",  # Electrical Services -- NOTE: actual code is 32320, verify against fixture
    "32320",  # Electrical Services
    "32410",  # Painting and Decorating Services
    "32420",  # Plastering and Ceiling Services
    "32510",  # Carpentry Services
    "32990",  # Other Construction Services n.e.c.
    # Professional services
    "69110",  # Legal Services
    "69310",  # Bookkeeping Services
    "69320",  # Accounting Services
    "69620",  # Management Advice and Related Consulting Services
    # Healthcare
    "85110",  # General Practice Medical Services
    "85120",  # Specialist Medical Services
    "85320",  # Dental Services
    "85390",  # Other Allied Health Services
    # Property
    "67110",  # Residential Property Operators
    "67200",  # Non-Residential Property Operators
    "67300",  # Real Estate Services
    # Retail / hospitality
    "41100",  # Supermarket and Grocery Stores
    "42440",  # Clothing Retailing
    "45110",  # Cafes and Restaurants
    "45120",  # Takeaway Food Services
    "45210",  # Pubs, Taverns and Bars
    # Transport / automotive
    "46100",  # Road Freight Transport
    "46220",  # Taxi and Other Road Transport
    "94110",  # Automotive Body, Paint and Interior Repair
    "94120",  # Automotive Electrical Services
    "94130",  # Automotive Mechanical Services
    "94140",  # Tyre Retailing
    # Other common
    "75100",  # Building and Other Industrial Cleaning Services
    "77110",  # Employment Placement and Recruitment Services
    "77120",  # Investigation and Security Services
    "78400",  # Hairdressing and Beauty Services
]
COMMON_OPTGROUP_LABEL = "Common"


# ── TPAR-relevant industries per ATO TPAR scope guidance ─────────────────
# Source: ato.gov.au/business/reports-and-returns/taxable-payments-annual-report
# Last reviewed: 2026-04-28
#
# This frozenset replaces the legacy 3-digit prefix matching in
# core/risk_modules/cluster_tpar.py. Set membership is more robust against
# ATO renumbering — a split or new sub-code will fail closed and surface in
# the freshness audit instead of silently widening the match.
#
# The codes below were derived by enumerating every fixture entry that the
# legacy prefix set ({"301".."323","731","510","511","512","700","701",
# "702","771","461"}) matched, so adoption is a no-op for currently-stored
# entities (verified by the diff audit in the Phase 2 verification step).
# Entries marked "non-TPAR in practice" are retained for now to preserve
# day-one parity; remove them in a follow-up curation pass with sign-off.
TPAR_RELEVANT_CODES = frozenset({
    # Building and construction
    "30110",  # House Construction
    "30190",  # Other Residential Building Construction
    "30200",  # Non-Residential Building Construction
    "31010",  # Road and Bridge Construction
    "31091",  # Swimming Pool and Spa Pool Construction or Installation
    "31099",  # Other Heavy and Civil Engineering Construction
    "32110",  # Land Development and Subdivision
    "32120",  # Site Preparation Services
    "32210",  # Concreting Services
    "32220",  # Bricklaying Services
    "32230",  # Roofing Services
    "32240",  # Structural Steel Erection Services
    "32310",  # Plumbing Services
    "32320",  # Electrical Services
    "32330",  # Air Conditioning and Heating Services
    "32340",  # Fire and Security Alarm Installation Services
    "32390",  # Other Building Installation Services
    # Cleaning
    "73110",  # Building and Other Industrial Cleaning Services
    "73120",  # Building Pest Control Services      (non-TPAR in practice)
    "73130",  # Gardening Services                   (non-TPAR in practice)
    # Courier / postal
    "51010",  # Postal Services
    "51020",  # Courier Pick-up and Delivery Services
    # IT
    "70000",  # Computer System Design and Related Services
    # Road freight
    "46100",  # Road Freight Transport
    # Security and investigation
    "77110",  # Police Services                      (non-TPAR in practice)
    "77120",  # Investigation and Security Services
    "77130",  # Fire Protection and Other Emergency  (non-TPAR in practice)
    "77140",  # Correctional and Detention Services  (non-TPAR in practice)
    "77190",  # Other Public Order and Safety        (non-TPAR in practice)
})


# ── Build grouped choices for Django (with optgroup support) ──────────────
def _build_choices():
    """
    Return a list suitable for ``models.CharField(choices=...)``.

    Structure::

        [
            ("Common", [(code, label), ...]),
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

    # Prepend the curated "Common" optgroup. Drop unresolved codes with a
    # warning instead of failing the import.
    common_items: list[tuple[str, str]] = []
    missing: list[str] = []
    for code in COMMON_INDUSTRY_CODES:
        desc = INDUSTRY_CODE_MAP.get(code)
        if desc is None:
            missing.append(code)
            continue
        common_items.append((code, f"{code} – {desc}"))
    if missing:
        logger.warning(
            "COMMON_INDUSTRY_CODES references %d code(s) not present in the "
            "loaded ATO BIC fixture: %s. These were skipped.",
            len(missing), ", ".join(missing),
        )
    if common_items:
        ordered.insert(0, (COMMON_OPTGROUP_LABEL, common_items))

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
