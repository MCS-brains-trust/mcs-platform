"""Views for the Franking Account ledger tab — company entities only."""
import json
import logging
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from config.authorization import get_financial_year_for_user
from core.models import DividendEvent, FinancialYear, FrankingAccountEntry

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def calculate_running_balances(entries):
    """Return list of dicts with entry + running_balance.

    Convention: credits increase balance, debits decrease balance.
    """
    balance = ZERO
    result = []
    for entry in entries:
        credit = entry.credit or ZERO
        debit = entry.debit or ZERO
        balance = balance + credit - debit
        result.append({
            "entry": entry,
            "running_balance": balance,
        })
    return result


def _get_opening_balance(entity, financial_year):
    """Opening balance = closing balance of the prior financial year.

    Closing balance = sum(credits) - sum(debits) for the prior year.
    Returns Decimal('0.00') if no prior year or no entries.
    """
    prior_fy = financial_year.prior_year
    if not prior_fy:
        # Try to find the most recent FY ending before this one
        prior_fy = (
            FinancialYear.objects.filter(
                entity=entity,
                end_date__lt=financial_year.start_date,
            )
            .order_by("-end_date")
            .first()
        )
    if not prior_fy:
        return ZERO

    entries = FrankingAccountEntry.objects.filter(
        entity=entity, financial_year=prior_fy,
    )
    total_credits = sum((e.credit or ZERO for e in entries), ZERO)
    total_debits = sum((e.debit or ZERO for e in entries), ZERO)
    return total_credits - total_debits


def _recalculate_dividend_events(entity, financial_year):
    """Update franking balances on all DividendEvents in this FY.

    For each event, the available balance at that point =
      opening_balance + credits on/before event date - debits on/before event date.
    """
    opening = _get_opening_balance(entity, financial_year)
    events = DividendEvent.objects.filter(financial_year=financial_year).order_by(
        "payment_date", "declaration_date",
    )
    for event in events:
        event_date = event.payment_date or event.declaration_date
        entries_up_to = FrankingAccountEntry.objects.filter(
            entity=entity,
            financial_year=financial_year,
            date__lte=event_date,
        )
        credits_to_date = sum((e.credit or ZERO for e in entries_up_to), ZERO)
        debits_to_date = sum((e.debit or ZERO for e in entries_up_to), ZERO)
        event.franking_account_opening_balance = opening
        event.franking_account_closing_balance = opening + credits_to_date - debits_to_date
        event.save(update_fields=[
            "franking_account_opening_balance",
            "franking_account_closing_balance",
        ])


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

@login_required
def franking_account_tab(request, pk):
    """Render the Franking Account tab content for a company financial year."""
    fy = get_financial_year_for_user(request, pk)
    entity = fy.entity

    entries = FrankingAccountEntry.objects.filter(
        entity=entity, financial_year=fy,
    ).order_by("date", "sort_order", "created_at")

    opening_balance = _get_opening_balance(entity, fy)
    entries_with_balances = calculate_running_balances(entries)

    # Adjust running balances to include opening balance
    for item in entries_with_balances:
        item["running_balance"] = opening_balance + item["running_balance"]

    total_credits = sum((e.credit or ZERO for e in entries), ZERO)
    total_debits = sum((e.debit or ZERO for e in entries), ZERO)
    closing_balance = opening_balance + total_credits - total_debits

    return render(request, "core/includes/franking_tab.html", {
        "fy": fy,
        "entity": entity,
        "entries_with_balances": entries_with_balances,
        "opening_balance": opening_balance,
        "closing_balance": closing_balance,
        "total_credits": total_credits,
        "total_debits": total_debits,
        "entry_type_choices": FrankingAccountEntry.ENTRY_TYPE_CHOICES,
    })


@login_required
@require_POST
def franking_entry_create(request, pk):
    """Create a new FrankingAccountEntry."""
    fy = get_financial_year_for_user(request, pk)
    entity = fy.entity

    if entity.entity_type != "company":
        return JsonResponse({"status": "error", "error": "Franking accounts are for companies only."}, status=400)

    try:
        data = json.loads(request.body) if request.content_type == "application/json" else request.POST
    except json.JSONDecodeError:
        data = request.POST

    date_val = data.get("date")
    description = data.get("description", "").strip()
    entry_type = data.get("entry_type", "")
    notes = data.get("notes", "").strip()

    try:
        debit = Decimal(str(data.get("debit", "") or "0"))
        credit = Decimal(str(data.get("credit", "") or "0"))
    except (InvalidOperation, ValueError):
        return JsonResponse({"status": "error", "error": "Invalid debit/credit amount."}, status=400)

    # Validation
    if not date_val:
        return JsonResponse({"status": "error", "error": "Date is required."}, status=400)
    if not entry_type:
        return JsonResponse({"status": "error", "error": "Entry type is required."}, status=400)
    if debit <= 0 and credit <= 0:
        return JsonResponse({"status": "error", "error": "Either debit or credit must be greater than zero."}, status=400)
    if debit > 0 and credit > 0:
        return JsonResponse({"status": "error", "error": "Enter either debit or credit, not both."}, status=400)

    FrankingAccountEntry.objects.create(
        entity=entity,
        financial_year=fy,
        date=date_val,
        description=description or dict(FrankingAccountEntry.ENTRY_TYPE_CHOICES).get(entry_type, ""),
        entry_type=entry_type,
        debit=debit if debit > 0 else None,
        credit=credit if credit > 0 else None,
        notes=notes,
        created_by=request.user,
    )

    _recalculate_dividend_events(entity, fy)

    if request.headers.get("HX-Request"):
        return franking_account_tab(request, pk)
    return redirect(f"{fy.get_absolute_url()}?tab=franking")


@login_required
@require_POST
def franking_entry_delete(request, pk, entry_pk):
    """Delete a FrankingAccountEntry."""
    fy = get_financial_year_for_user(request, pk)
    entity = fy.entity

    entry = get_object_or_404(
        FrankingAccountEntry, pk=entry_pk, entity=entity, financial_year=fy,
    )
    entry.delete()

    _recalculate_dividend_events(entity, fy)

    if request.headers.get("HX-Request"):
        return franking_account_tab(request, pk)
    return redirect(f"{fy.get_absolute_url()}?tab=franking")


@login_required
def franking_account_summary_api(request, pk):
    """JSON summary of franking account for use by the dividend wizard."""
    fy = get_financial_year_for_user(request, pk)
    entity = fy.entity

    entries = FrankingAccountEntry.objects.filter(entity=entity, financial_year=fy)
    opening_balance = _get_opening_balance(entity, fy)
    total_credits = sum((e.credit or ZERO for e in entries), ZERO)
    total_debits = sum((e.debit or ZERO for e in entries), ZERO)
    closing_balance = opening_balance + total_credits - total_debits

    TWO_DP = Decimal("0.01")
    return JsonResponse({
        "opening_balance": str(opening_balance.quantize(TWO_DP)),
        "closing_balance": str(closing_balance.quantize(TWO_DP)),
        "total_credits": str(total_credits.quantize(TWO_DP)),
        "total_debits": str(total_debits.quantize(TWO_DP)),
        "entry_count": entries.count(),
    })
