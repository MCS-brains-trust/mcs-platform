"""GST splitting for cash-basis (Cashbook) journals.

An accountant working a cash-basis client journals the period's transactions
straight into the trial balance — the journal IS the transaction record, so
GST has to be accounted for inside it. The accountant keys the GROSS figure
off the invoice and a tax code per line; this module rewrites those lines to
net and appends exactly two 3380 control lines.

Only ``JournalType.CASHBOOK`` journals are touched. Every other type is left
byte-identical, because general journals legitimately carry already-net
figures — Hazaway JE-002 ("migrated previous accountant profit & loss") posts
another accountant's net P&L to GST-coded accounts, and splitting it would
strip 1/11th out of every line.
"""
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction

from .bas_utils import TAXABLE_CODES, normalise_tax_treatment

GST_CONTROL_CODE = "3380"
GST_CONTROL_NAME = "GST payable control account"
GST_CONTROL_STANDARD_CODE = "BS-CL-006"
CENTS = Decimal("0.01")
ELEVEN = Decimal("11")


def resolve_line_tax_code(entity, account_code):
    """The chart's default tax treatment for an account, normalised.

    EntityChartOfAccount first, then the entity-type template, then blank.
    Always normalised: account 1946 Tools carries 'inp' in lowercase on the
    live file, and a bare ``tax_code in TAXABLE_CODES`` test would silently
    treat Tools as GST-free.
    """
    from .models import ChartOfAccount, EntityChartOfAccount, template_entity_type

    ecoa = EntityChartOfAccount.objects.filter(
        entity=entity, account_code=account_code,
    ).first()
    if ecoa:
        return normalise_tax_treatment(ecoa.tax_code)

    coa = ChartOfAccount.objects.filter(
        entity_type=template_entity_type(entity.entity_type),
        account_code=account_code,
    ).first()
    if coa:
        return normalise_tax_treatment(coa.tax_code)
    return ""


def line_gst(gross, tax_code, override=None):
    """GST on one line.

    An override is respected on a taxable line — it carries partial input tax
    credits (business-use apportionment) and the non-creditable remainder stays
    in the expense, because net is always ``gross - gst``.

    On a non-taxable line the override is forced to nil rather than trusted.
    Without that, changing a line from GST to FRE would leave the stale figure
    behind and quietly overstate the input credit.
    """
    code = normalise_tax_treatment(tax_code)
    if code not in TAXABLE_CODES:
        return Decimal("0.00")
    if override is not None:
        return Decimal(override).quantize(CENTS, rounding=ROUND_HALF_UP)
    return (Decimal(gross) / ELEVEN).quantize(CENTS, rounding=ROUND_HALF_UP)


def _gross_of(line):
    """The gross the accountant keyed, reconstructed rather than stored.

    ``gross = (debit or credit) + gst_amount`` is what makes the split
    idempotent: re-splitting reconstructs 1,990.40 from 1,809.45 + 180.95 and
    lands in the same place. A stored gross field would be a fourth value that
    can drift out of agreement with the other three.
    """
    return max(line.debit, line.credit) + (line.gst_amount or Decimal("0"))


def ensure_gst_control_mapping(entity):
    """Point 3380 at the standard 'GST payable' balance-sheet line.

    Without a ClientAccountMapping, docgen falls back to keyword/code-range
    classification for the account: the liability still appears under current
    liabilities, but not badged as the standard line. Six of the nine live
    entities already have this mapping from the bank-posting path; the two that
    do not have a 3380 balance sitting unmapped today.

    Never overwrites an existing mapping — the accountant may have pointed the
    account somewhere deliberately.
    """
    from .models import AccountMapping, ClientAccountMapping

    if ClientAccountMapping.objects.filter(
        entity=entity, client_account_code=GST_CONTROL_CODE,
    ).exists():
        return

    item = AccountMapping.objects.filter(
        standard_code=GST_CONTROL_STANDARD_CODE,
    ).first()
    if not item:
        return

    ClientAccountMapping.objects.create(
        entity=entity,
        client_account_code=GST_CONTROL_CODE,
        client_account_name=GST_CONTROL_NAME,
        mapped_line_item=item,
    )


@transaction.atomic
def split_cashbook_journal(journal):
    """Rewrite a Cashbook journal's gross lines to net plus two 3380 lines.

    Idempotent. Raises ``ValueError`` if the result does not balance, which
    rolls the whole rewrite back.
    """
    from .models import AdjustingJournal, JournalLine

    if journal.journal_type != AdjustingJournal.JournalType.CASHBOOK:
        return

    entity = journal.financial_year.entity

    # Regenerate, never patch: the previous pair is deleted outright. Matching
    # on is_gst_control rather than account_code is what lets an accountant
    # keep their own 3380 line (the quarterly ATO payment) through a re-split.
    journal.lines.filter(is_gst_control=True).delete()

    source_lines = list(journal.lines.order_by("line_number", "id"))

    gst_on_credits = Decimal("0.00")
    gst_on_debits = Decimal("0.00")
    next_line_number = 0

    for line in source_lines:
        next_line_number = max(next_line_number, line.line_number)
        gross = _gross_of(line)

        tax_code = normalise_tax_treatment(line.tax_code)
        if not tax_code:
            tax_code = resolve_line_tax_code(entity, line.account_code)

        # The override is read from its own field, never from gst_amount.
        # gst_amount is purely derived — it is the figure this split writes —
        # and gross is reconstructed by adding it back. If the two shared one
        # field, a first split with an override would read 1,990.40 + 145.50
        # as the gross and inflate the line by the override.
        gst = line_gst(gross, tax_code, override=line.gst_override)

        is_credit = line.credit > line.debit
        line.tax_code = tax_code
        line.gst_amount = gst
        if is_credit:
            line.credit = gross - gst
            line.debit = Decimal("0.00")
            gst_on_credits += gst
        else:
            line.debit = gross - gst
            line.credit = Decimal("0.00")
            gst_on_debits += gst
        # A non-taxable line cannot carry an override: line_gst() forces the
        # GST to nil, so clear the stored figure too rather than leaving a
        # stale number that would reappear if the code were changed back.
        if gst == 0:
            line.gst_override = None
        line.save(update_fields=[
            "debit", "credit", "tax_code", "gst_amount", "gst_override",
        ])

    if gst_on_credits or gst_on_debits:
        ensure_gst_control_mapping(entity)

    if gst_on_credits:
        next_line_number += 1
        JournalLine.objects.create(
            journal=journal, line_number=next_line_number,
            account_code=GST_CONTROL_CODE, account_name=GST_CONTROL_NAME,
            description="GST collected",
            debit=Decimal("0.00"), credit=gst_on_credits,
            tax_code="N-T", gst_amount=Decimal("0.00"),
            gst_override=None, is_gst_control=True,
        )
    if gst_on_debits:
        next_line_number += 1
        JournalLine.objects.create(
            journal=journal, line_number=next_line_number,
            account_code=GST_CONTROL_CODE, account_name=GST_CONTROL_NAME,
            description="GST paid",
            debit=gst_on_debits, credit=Decimal("0.00"),
            tax_code="N-T", gst_amount=Decimal("0.00"),
            gst_override=None, is_gst_control=True,
        )

    # Every line satisfies net + gst = gross, and the control lines carry the
    # GST sums on the matching side, so a gross journal that balanced must
    # still balance. If it does not, something above is wrong — refuse rather
    # than post a broken journal.
    final = list(journal.lines.all())
    total_dr = sum(l.debit for l in final)
    total_cr = sum(l.credit for l in final)
    if total_dr != total_cr:
        raise ValueError(
            f"Cashbook GST split did not balance: "
            f"Dr {total_dr} != Cr {total_cr} on {journal.reference_number}"
        )

    journal.recalculate_totals()
