"""A chart row behind every trial-balance line.

The allocation/COA picker is built from EntityChartOfAccount alone, so a code
that reaches a TrialBalanceLine without a chart row cannot be allocated
against — the money is visible in the trial balance and unreachable in the
picker. `commit_tb_import` has kept the chart in step since 2026-03-10, but
it is one writer of twenty-six, and the others (the HandiLedger entity
import, manual journals, the roll-forward, the bulk import) each left live
orphans behind. The guarantee therefore lives at the point the row is
written, via a post_save receiver, not in any caller.
"""
import logging

logger = logging.getLogger(__name__)

# _hl_section_for_code returns these display names verbatim, and they are NOT
# hyphenated. commit_tb_import's own copy of this map keys "Non-Current
# Liabilities" and "Non-Current Assets" WITH a hyphen, so those two never
# matched and every non-current account it created fell through to SUSPENSE.
_DISPLAY_SECTION_TO_COA = {
    "Income": "revenue",
    "Cost of Sales": "cost_of_sales",
    "Expenses": "expenses",
    "Current Assets": "assets",
    "Non Current Assets": "assets",
    "Current Liabilities": "liabilities",
    "Non Current Liabilities": "liabilities",
    "Equity": "equity",
}


def section_for_code(account_code):
    """Best-effort statement section from the account code alone.

    Suspense when the code says nothing — an account parked there is visible
    and fixable, which a wrong section is not.
    """
    from core.models import EntityChartOfAccount
    from core.views import _hl_section_for_code  # lazy: views imports models

    display = _hl_section_for_code(account_code)
    return _DISPLAY_SECTION_TO_COA.get(
        display, EntityChartOfAccount.StatementSection.SUSPENSE)


def ensure_chart_account(entity, account_code, account_name=None,
                         mapped_line_item=None):
    """Idempotently guarantee (entity, account_code) exists in the chart.

    Returns ``(row, created)``.

    An existing row is authoritative: its name and section are left alone,
    because the chart is where an accountant's naming lives and a TB file is
    untrusted input. The one thing filled in is ``maps_to`` when it is unset,
    so a mapping chosen during import is not thrown away.
    """
    from core.models import EntityChartOfAccount

    if not account_code:
        return None, False

    existing = EntityChartOfAccount.objects.filter(
        entity=entity, account_code=account_code).first()
    if existing:
        if mapped_line_item is not None and existing.maps_to_id is None:
            existing.maps_to = mapped_line_item
            existing.save(update_fields=["maps_to"])
        return existing, False

    return EntityChartOfAccount.objects.create(
        entity=entity,
        account_code=account_code,
        account_name=account_name or account_code,
        section=section_for_code(account_code),
        maps_to=mapped_line_item,
        is_active=True,
        # Not from the standard template — flagged so a template rebuild and
        # the COA tab can tell the two apart.
        is_custom=True,
    ), True
