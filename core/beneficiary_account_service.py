"""
StatementHub — Per-Beneficiary 4xxx Account Materialisation
============================================================
Materialises per-officer child accounts (4053.01 Physical distribution —
Elvis Chiaravalle, etc.) on trust entities for distribution, beneficiary
current account, beneficiary loan, and corporate-beneficiary parent codes.

Parallels core/capital_account_service.py (which handles the 9000-series
capital accounts). Phase 3 will migrate the 9000-series to this pipeline
and remove core/capital_account_service.py.

See:
    per_beneficiary_accounts_phase1_findings.md (audit)
    per_beneficiary_accounts_phase2.md (design)
"""
import logging
import re
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Canonical 38-code constant — frozen by Phase 1.5 audit + Phase 2 review.
# Do NOT load from DB at import time. Constant must be deterministic.
# ---------------------------------------------------------------------------
BENEFICIARY_PARENT_CODES = [
    # Group A — Beneficiary capital accounts
    {"code": "4000", "name": "Opening balance - Beneficiary",  "section": "capital_accounts", "group": "A", "requires_company_beneficiary": False},
    {"code": "4003", "name": "Interest received on loan",      "section": "capital_accounts", "group": "A", "requires_company_beneficiary": False},
    {"code": "4004", "name": "Funds loaned to trust",           "section": "capital_accounts", "group": "A", "requires_company_beneficiary": False},
    {"code": "4005", "name": "Distribution for year",           "section": "capital_accounts", "group": "A", "requires_company_beneficiary": False},
    {"code": "4050", "name": "Income tax withheld",             "section": "capital_accounts", "group": "A", "requires_company_beneficiary": False},
    {"code": "4051", "name": "Advance maintenance/education",   "section": "capital_accounts", "group": "A", "requires_company_beneficiary": False},
    {"code": "4052", "name": "Interest on loan",                "section": "capital_accounts", "group": "A", "requires_company_beneficiary": False},
    {"code": "4053", "name": "Physical distribution",           "section": "capital_accounts", "group": "A", "requires_company_beneficiary": False},
    # Group B — Distribution P&L appropriation
    {"code": "4020", "name": "Distribution for year",           "section": "equity",            "group": "B", "requires_company_beneficiary": False},
    {"code": "4021", "name": "Distribution for year - prior",   "section": "equity",            "group": "B", "requires_company_beneficiary": False},
    # Group C — Beneficiary current account (only the lowest slot is a parent)
    {"code": "4100", "name": "Beneficiary current account",     "section": "equity",            "group": "C", "requires_company_beneficiary": False},
    # Group D — Beneficiary loans
    {"code": "4110", "name": "Funds loaned to trust",           "section": "liabilities",       "group": "D", "requires_company_beneficiary": False},
    # Group E — Beneficiary interest
    {"code": "4120", "name": "Interest on loan",                "section": "expenses",          "group": "E", "requires_company_beneficiary": False},
    # Group F — UPE corporate beneficiary family
    {"code": "4400", "name": "Opening balance -Corp benef'y- UPE",     "section": "equity", "group": "F", "requires_company_beneficiary": True},
    {"code": "4403", "name": "Interest received on loan",              "section": "equity", "group": "F", "requires_company_beneficiary": True},
    {"code": "4404", "name": "Funds loaned to trust",                  "section": "equity", "group": "F", "requires_company_beneficiary": True},
    {"code": "4405", "name": "Distribution for year",                  "section": "equity", "group": "F", "requires_company_beneficiary": True},
    {"code": "4450", "name": "Income tax withheld",                    "section": "equity", "group": "F", "requires_company_beneficiary": True},
    {"code": "4451", "name": "Advance maintenance/education",          "section": "equity", "group": "F", "requires_company_beneficiary": True},
    {"code": "4452", "name": "Interest on loan",                       "section": "equity", "group": "F", "requires_company_beneficiary": True},
    {"code": "4453", "name": "Physical distribution",                  "section": "equity", "group": "F", "requires_company_beneficiary": True},
    # Group G — Sub-trust corporate beneficiary family
    {"code": "4500", "name": "Opening balance -Corp benef'y- Sub-trust", "section": "equity", "group": "G", "requires_company_beneficiary": True},
    {"code": "4503", "name": "Interest received on loan",                "section": "equity", "group": "G", "requires_company_beneficiary": True},
    {"code": "4504", "name": "Funds loaned to trust",                    "section": "equity", "group": "G", "requires_company_beneficiary": True},
    {"code": "4505", "name": "Distribution for year",                    "section": "equity", "group": "G", "requires_company_beneficiary": True},
    {"code": "4550", "name": "Income tax withheld",                      "section": "equity", "group": "G", "requires_company_beneficiary": True},
    {"code": "4551", "name": "Advance maintenance/education",            "section": "equity", "group": "G", "requires_company_beneficiary": True},
    {"code": "4552", "name": "Interest on loan",                         "section": "equity", "group": "G", "requires_company_beneficiary": True},
    {"code": "4553", "name": "Physical distribution",                    "section": "equity", "group": "G", "requires_company_beneficiary": True},
]

# Slot codes superseded by per-officer decimal-suffix model — to be removed
# from master template and from per-entity ECAs (when safe). These were
# placeholder slots in the trust template (e.g. "Beneficiary current
# account - 2") that pre-dated the per-officer materialisation pattern.
SLOT_CODES_TO_REMOVE = [
    "4101", "4102", "4103",  # Beneficiary current account - 2/3/4
    "4111", "4112", "4113",  # Funds loaned to trust - Beneficiary 2/3/4
    "4121", "4122", "4123",  # Interest on loan - Beneficiary 2/3/4
]

# Codes where unit-holder naming overrides the default "[parent name] — [Name]"
# Per platform principle: unit holder loan accounts are labelled
# "Unitholders' funds introduced — [Name]".
_UNIT_HOLDER_FUNDS_LOANED_CODES = {"4004", "4404", "4504"}

# Set of parent codes (for fast membership tests)
_PARENT_CODE_SET = {entry["code"] for entry in BENEFICIARY_PARENT_CODES}


def _today():
    return timezone.now().date()


def _build_account_name(entry, officer):
    """Compute the materialised child account name from a parent entry + officer.

    Unit-holder override applies to the "Funds loaned to trust" codes
    (4004 / 4404 / 4504) when the officer is a unit holder.
    """
    from core.models import EntityOfficer
    if (
        officer.role == EntityOfficer.OfficerRole.UNIT_HOLDER
        and entry["code"] in _UNIT_HOLDER_FUNDS_LOANED_CODES
    ):
        return f"Unitholders' funds introduced — {officer.full_name}"
    return f"{entry['name']} — {officer.full_name}"


def _has_postings(entity, account_code):
    """True if any TB / journal posting references this code on this entity."""
    from core.models import TrialBalanceLine, JournalLine, AdjustingJournal

    if TrialBalanceLine.objects.filter(
        financial_year__entity=entity, account_code=account_code
    ).exists():
        return True
    if JournalLine.objects.filter(
        journal__financial_year__entity=entity, account_code=account_code
    ).exists():
        return True
    if AdjustingJournal.objects.filter(
        financial_year__entity=entity, lines__account_code=account_code,
    ).exists():
        return True
    return False


def _ghost_already_cleaned(entity):
    """True iff the per-officer materialisation has previously run on this entity.

    Detected by the presence of any auto-provisioned ECA whose code starts
    with one of the 38 parent codes followed by a dot.
    """
    from core.models import EntityChartOfAccount
    qs = EntityChartOfAccount.objects.filter(
        entity=entity, auto_provisioned=True, beneficiary_officer__isnull=False,
    )
    for eca in qs.only("account_code")[:50]:
        prefix = (eca.account_code or "").split(".")[0]
        if prefix in _PARENT_CODE_SET:
            return True
    return False


def _cleanup_ghost_rows(entity):
    """Delete unlinked ghost ECA rows that match parent.NN pattern within scope.

    Runs once per entity (gated by `_ghost_already_cleaned`). Skips any ghost
    that has postings — those are escalated to logs + ActivityLog.
    """
    from core.models import EntityChartOfAccount, ActivityLog

    if _ghost_already_cleaned(entity):
        return {"deleted": 0, "escalated": []}

    parent_alt = "|".join(re.escape(c) for c in sorted(_PARENT_CODE_SET))
    pattern = rf"^({parent_alt})\.\d{{2}}$"
    ghost_qs = EntityChartOfAccount.objects.filter(
        entity=entity,
        auto_provisioned=False,
        beneficiary_officer__isnull=True,
        account_code__regex=pattern,
    )

    deleted = 0
    escalated = []
    with transaction.atomic():
        for ghost in list(ghost_qs):
            if _has_postings(entity, ghost.account_code):
                logger.warning(
                    "Ghost row escalation — entity=%s code=%s name=%s "
                    "(postings exist; not deleted)",
                    entity.id, ghost.account_code, ghost.account_name,
                )
                escalated.append({
                    "code": ghost.account_code,
                    "name": ghost.account_name,
                })
                continue
            ghost.delete()
            deleted += 1

    if escalated:
        try:
            fy = entity.financial_years.order_by("-end_date").first()
            if fy:
                ActivityLog.objects.create(
                    financial_year=fy,
                    event_type="general",
                    title="Ghost row escalation",
                    description=(
                        f"{len(escalated)} ghost ECA rows on entity "
                        f"{entity.entity_name} have postings and were not "
                        f"deleted: "
                        + ", ".join(f"{g['code']} {g['name']}" for g in escalated)
                    ),
                )
        except Exception:
            logger.exception("Failed to record ghost-row escalation activity")

    if deleted or escalated:
        logger.info(
            "Ghost row cleanup on entity %s: deleted=%d escalated=%d",
            entity.id, deleted, len(escalated),
        )
    return {"deleted": deleted, "escalated": escalated}


def _cleanup_slot_codes(entity):
    """Delete the 9 superseded slot codes (4101..4103, 4111..4113, 4121..4123)
    from this entity's ECAs where safe.

    Rules per Phase 2 review:
      - is_custom=True → keep, log warning
      - postings exist → escalate (do not delete)
      - is_custom=False AND no postings → delete
    """
    from core.models import EntityChartOfAccount, ActivityLog

    qs = EntityChartOfAccount.objects.filter(
        entity=entity, account_code__in=SLOT_CODES_TO_REMOVE,
    )
    deleted = 0
    escalated = []
    retained_custom = []
    with transaction.atomic():
        for eca in list(qs):
            if eca.is_custom:
                retained_custom.append(eca.account_code)
                logger.warning(
                    "Slot code retained (is_custom=True): entity=%s code=%s",
                    entity.id, eca.account_code,
                )
                continue
            if _has_postings(entity, eca.account_code):
                escalated.append({"code": eca.account_code, "name": eca.account_name})
                logger.warning(
                    "Slot code escalation — entity=%s code=%s "
                    "(postings exist; not deleted)",
                    entity.id, eca.account_code,
                )
                continue
            eca.delete()
            deleted += 1

    if escalated:
        try:
            fy = entity.financial_years.order_by("-end_date").first()
            if fy:
                ActivityLog.objects.create(
                    financial_year=fy,
                    event_type="general",
                    title="Slot code escalation",
                    description=(
                        f"{len(escalated)} slot codes on entity "
                        f"{entity.entity_name} have postings and were not "
                        f"deleted: "
                        + ", ".join(f"{g['code']} {g['name']}" for g in escalated)
                    ),
                )
        except Exception:
            logger.exception("Failed to record slot-code escalation activity")

    return {"deleted": deleted, "escalated": escalated, "retained_custom": retained_custom}


def _resolve_officer_suffix(entity, officer):
    """Return a `.NN`-format suffix unique to this officer on this entity.

    Default = `.{display_order:02d}`. If that suffix is already used by a
    different officer's auto-provisioned ECA, scan .01..99 for the first
    unused suffix. Last resort: `.<pk-fragment>`.
    """
    from core.models import EntityChartOfAccount

    desired = f".{officer.display_order:02d}"
    probe_parent = BENEFICIARY_PARENT_CODES[0]["code"]
    probe_code = f"{probe_parent}{desired}"

    collision = EntityChartOfAccount.objects.filter(
        entity=entity, account_code=probe_code, auto_provisioned=True,
    ).exclude(beneficiary_officer=officer).exclude(
        beneficiary_officer__isnull=True
    ).exists()
    if not collision:
        return desired

    used = set(
        EntityChartOfAccount.objects.filter(
            entity=entity,
            account_code__startswith=f"{probe_parent}.",
            auto_provisioned=True,
        ).values_list("account_code", flat=True)
    )
    for n in range(1, 100):
        candidate = f".{n:02d}"
        if f"{probe_parent}{candidate}" not in used:
            return candidate
    # 99-officer ceiling exceeded — degrade to officer pk fragment
    return f".{str(officer.pk)[:4]}"


def provision_beneficiary_accounts(officer_id):
    """Materialise per-officer child accounts under the 38 parent codes.

    Idempotent — uses get_or_create keyed on (entity, account_code).
    """
    from core.models import EntityOfficer, EntityChartOfAccount

    try:
        officer = EntityOfficer.objects.select_related("entity").get(pk=officer_id)
    except EntityOfficer.DoesNotExist:
        logger.warning("provision_beneficiary_accounts: officer %s not found", officer_id)
        return 0

    if officer.role not in EntityOfficer.DISTRIBUTION_ROLES:
        return 0

    entity = officer.entity
    if entity.entity_type != "trust":
        return 0

    # First-officer-on-entity housekeeping
    _cleanup_slot_codes(entity)
    _cleanup_ghost_rows(entity)

    # Suffix from display_order. display_order is normally auto-assigned on
    # save (core/models.py:557-564) but legacy officers sometimes share
    # display_order=0 (created via paths that bypassed the auto-assign,
    # e.g. XPM bulk import). Detect collision with another officer's
    # existing children and fall back to the next free two-digit suffix
    # (then to the officer.pk fragment as last resort) — mirrors the
    # 9000-series pattern at capital_account_service.py:79-82.
    suffix = _resolve_officer_suffix(entity, officer)

    today = _today()
    is_ceased = bool(officer.date_ceased and officer.date_ceased <= today)
    is_company = (officer.beneficiary_type == "company")

    created = 0
    skipped = 0
    with transaction.atomic():
        for idx, entry in enumerate(BENEFICIARY_PARENT_CODES):
            if entry["requires_company_beneficiary"] and not is_company:
                skipped += 1
                continue

            account_code = f"{entry['code']}{suffix}"
            account_name = _build_account_name(entry, officer)

            parent_eca = EntityChartOfAccount.objects.filter(
                entity=entity, account_code=entry["code"],
            ).first()
            maps_to = parent_eca.maps_to if parent_eca else None

            _, was_created = EntityChartOfAccount.objects.get_or_create(
                entity=entity,
                account_code=account_code,
                defaults={
                    "account_name": account_name,
                    "section": entry["section"],
                    "maps_to": maps_to,
                    "is_active": True,
                    "is_custom": False,
                    "auto_provisioned": True,
                    "beneficiary_officer": officer,
                    "is_ceased": is_ceased,
                    "display_order": idx,
                },
            )
            if was_created:
                created += 1

    if created:
        logger.info(
            "Provisioned %d beneficiary accounts for officer %s on entity %s "
            "(skipped %d due to F/G gating)",
            created, officer.full_name, entity.entity_name, skipped,
        )
    return created


def sync_officer_account_names(officer_id):
    """Update account_name on every auto-provisioned ECA linked to this officer.

    Called from post_save (existing officer save). Idempotent — same-name
    save = no DB update.
    """
    from core.models import EntityOfficer, EntityChartOfAccount

    try:
        officer = EntityOfficer.objects.select_related("entity").get(pk=officer_id)
    except EntityOfficer.DoesNotExist:
        return 0

    if officer.role not in EntityOfficer.DISTRIBUTION_ROLES:
        return 0

    parent_lookup = {entry["code"]: entry for entry in BENEFICIARY_PARENT_CODES}

    updated = 0
    with transaction.atomic():
        ecas = EntityChartOfAccount.objects.filter(
            beneficiary_officer=officer, auto_provisioned=True,
        )
        for eca in ecas:
            parent_code = (eca.account_code or "").split(".")[0]
            entry = parent_lookup.get(parent_code)
            if not entry:
                # 9000-series or anything else not in our scope — leave alone.
                continue
            new_name = _build_account_name(entry, officer)
            if new_name != eca.account_name:
                EntityChartOfAccount.objects.filter(pk=eca.pk).update(
                    account_name=new_name
                )
                updated += 1

    if updated:
        logger.info(
            "Synced %d account names for officer %s",
            updated, officer.full_name,
        )
    return updated


def count_parent_postings_with_children(financial_year):
    """Count distinct parent-code postings on this FY whose entity has at
    least one auto-provisioned officer-linked child for that parent.

    Drives the read-only banner on the journal entry screen.
    """
    from core.models import TrialBalanceLine, EntityChartOfAccount

    entity = financial_year.entity
    parent_codes = sorted(_PARENT_CODE_SET)

    posted_parents = set(
        TrialBalanceLine.objects.filter(
            financial_year=financial_year,
            account_code__in=parent_codes,
        ).values_list("account_code", flat=True)
    )
    if not posted_parents:
        return 0

    count = 0
    for parent in posted_parents:
        if EntityChartOfAccount.objects.filter(
            entity=entity,
            account_code__startswith=f"{parent}.",
            auto_provisioned=True,
            beneficiary_officer__isnull=False,
        ).exists():
            count += 1
    return count
