"""
MCS Platform - Audit Risk & Chart of Accounts Views
Provides the Chart of Accounts management page, Audit Library page,
Risk Engine execution, Risk Flags review, and AI risk analysis.
"""
import json
import hashlib
import logging
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q, Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST
from config.authorization import get_financial_year_for_user

from .models import (
    AccountMapping, ChartOfAccount, RiskRule, RiskReferenceData, RiskFlag,
    FinancialYear, Entity, Client,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Chart of Accounts
# ---------------------------------------------------------------------------
@login_required
def chart_of_accounts(request):
    """
    Display the MC&S entity-type-specific chart of accounts.
    Grouped by section, with entity type tabs.
    """
    query = request.GET.get("q", "")
    entity_type_filter = request.GET.get("entity_type", "company")
    section_filter = request.GET.get("section", "")

    accounts = ChartOfAccount.objects.filter(
        entity_type=entity_type_filter, is_active=True
    )

    if query:
        accounts = accounts.filter(
            Q(account_code__icontains=query)
            | Q(account_name__icontains=query)
            | Q(classification__icontains=query)
        )

    if section_filter:
        accounts = accounts.filter(section=section_filter)

    accounts = accounts.order_by("section", "display_order")

    # Group by section
    grouped = {}
    for acc in accounts:
        section_label = acc.get_section_display()
        if section_label not in grouped:
            grouped[section_label] = []
        grouped[section_label].append(acc)

    # Get counts per entity type for the tabs
    entity_counts = {}
    for et_value, et_label in Entity.EntityType.choices:
        if et_value == "smsf":
            continue
        entity_counts[et_value] = {
            "label": et_label,
            "count": ChartOfAccount.objects.filter(
                entity_type=et_value, is_active=True
            ).count(),
        }

    context = {
        "grouped_accounts": grouped,
        "total_accounts": ChartOfAccount.objects.filter(
            entity_type=entity_type_filter, is_active=True
        ).count(),
        "total_all": ChartOfAccount.objects.filter(is_active=True).count(),
        "query": query,
        "entity_type_filter": entity_type_filter,
        "section_filter": section_filter,
        "section_choices": ChartOfAccount.StatementSection.choices,
        "entity_counts": entity_counts,
    }
    return render(request, "core/chart_of_accounts.html", context)


@login_required
def chart_of_accounts_api(request):
    """
    JSON API endpoint for chart of accounts.
    Used by the bank statement review page and other AJAX consumers.
    """
    entity_type = request.GET.get("entity_type", "company")
    section = request.GET.get("section", "")
    q = request.GET.get("q", "")

    qs = ChartOfAccount.objects.filter(
        entity_type=entity_type, is_active=True
    ).order_by("section", "display_order")

    if section:
        qs = qs.filter(section=section)
    if q:
        qs = qs.filter(
            Q(account_code__icontains=q) | Q(account_name__icontains=q)
        )

    accounts = [
        {
            "code": a.account_code,
            "name": a.account_name,
            "section": a.get_section_display(),
            "tax": a.tax_code,
            "classification": a.classification,
        }
        for a in qs[:500]
    ]
    return JsonResponse({"accounts": accounts})


# ---------------------------------------------------------------------------
# Chart of Accounts — Add / Edit / Delete
# ---------------------------------------------------------------------------

# Account code ranges by section
CODE_RANGES = {
    'revenue':          (0, 999,    'Revenue'),
    'cost_of_sales':    (0, 999,    'Cost of Sales'),
    'expenses':         (1000, 1999, 'Expenses'),
    'assets':           (2000, 2999, 'Assets'),  # 2000-2499 Current, 2500-2999 Non-Current
    'liabilities':      (3000, 3999, 'Liabilities'),  # 3000-3499 Current, 3500-3999 NCL
    'equity':           (4000, 4999, 'Equity'),
    'capital_accounts': (4000, 4999, 'Capital Accounts'),
    'pl_appropriation': (4000, 4999, 'P&L Appropriation'),
    'suspense':         (9000, 9999, 'Suspense'),
}


def _section_for_code(code_str):
    """
    Determine the expected section(s) for a given numeric account code.
    Returns a list of (section_value, label) tuples.
    """
    try:
        code_int = int(code_str.split('.')[0])
    except (ValueError, IndexError):
        return []
    results = []
    if 0 <= code_int <= 999:
        results.append(('revenue', 'Revenue / Cost of Sales'))
    elif 1000 <= code_int <= 1999:
        results.append(('expenses', 'Expenses'))
    elif 2000 <= code_int <= 2499:
        results.append(('assets', 'Current Assets'))
    elif 2500 <= code_int <= 2999:
        results.append(('assets', 'Non-Current Assets'))
    elif 3000 <= code_int <= 3499:
        results.append(('liabilities', 'Current Liabilities'))
    elif 3500 <= code_int <= 3999:
        results.append(('liabilities', 'Non-Current Liabilities'))
    elif 4000 <= code_int <= 4999:
        results.append(('equity', 'Equity / Capital Accounts'))
    return results


def _validate_code_section(code_str, section):
    """
    Validate that the account code falls within the allowed range for the section.
    Returns (is_valid, error_message).
    """
    try:
        code_int = int(code_str.split('.')[0])
    except (ValueError, IndexError):
        return False, 'Account code must start with a numeric value.'

    allowed = {
        'revenue':          (0, 999),
        'cost_of_sales':    (0, 999),
        'expenses':         (1000, 1999),
        'assets':           (2000, 2999),
        'liabilities':      (3000, 3999),
        'equity':           (4000, 4999),
        'capital_accounts': (4000, 4999),
        'pl_appropriation': (4000, 4999),
        'suspense':         (9000, 9999),
    }
    if section not in allowed:
        return False, f'Unknown section: {section}'
    lo, hi = allowed[section]
    if not (lo <= code_int <= hi):
        return False, f'Code {code_str} must be between {lo} and {hi} for {dict(ChartOfAccount.StatementSection.choices).get(section, section)}.'
    return True, ''


@login_required
def coa_add(request):
    """
    Add a new account to the Chart of Accounts.
    """
    entity_type = request.GET.get('entity_type', 'company')
    return_to_fy = request.GET.get('fy', '')  # Financial year PK to return to

    if request.method == 'POST':
        entity_type = request.POST.get('entity_type', 'company')
        return_to_fy = request.POST.get('return_to_fy', '')
        account_code = request.POST.get('account_code', '').strip()
        account_name = request.POST.get('account_name', '').strip()
        section = request.POST.get('section', '')
        classification = request.POST.get('classification', '').strip()
        tax_code = request.POST.get('tax_code', '').strip()
        maps_to_id = request.POST.get('maps_to', '')

        errors = []
        if not account_code:
            errors.append('Account code is required.')
        if not account_name:
            errors.append('Account name is required.')
        if not section:
            errors.append('Section is required.')

        # Validate code range
        if account_code and section:
            valid, err = _validate_code_section(account_code, section)
            if not valid:
                errors.append(err)

        # Check uniqueness
        if account_code and ChartOfAccount.objects.filter(
            entity_type=entity_type, account_code=account_code
        ).exists():
            errors.append(f'Account code {account_code} already exists for this entity type.')

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            maps_to = None
            if maps_to_id:
                maps_to = AccountMapping.objects.filter(pk=maps_to_id).first()

            # Determine display_order: place after the last account in this section with a lower code
            last = ChartOfAccount.objects.filter(
                entity_type=entity_type, section=section
            ).order_by('-display_order').first()
            display_order = (last.display_order + 10) if last else 10

            ChartOfAccount.objects.create(
                entity_type=entity_type,
                account_code=account_code,
                account_name=account_name,
                section=section,
                classification=classification,
                tax_code=tax_code,
                maps_to=maps_to,
                display_order=display_order,
                is_active=True,
            )
            messages.success(request, f'Account {account_code} — {account_name} added successfully.')
            if return_to_fy:
                return redirect(f"/years/{return_to_fy}/trial-balance/")
            return redirect(f"/chart-of-accounts/?entity_type={entity_type}")

    # Get AccountMapping options for the maps_to dropdown
    mapping_options = AccountMapping.objects.all().order_by('financial_statement', 'display_order')

    context = {
        'entity_type': entity_type,
        'return_to_fy': return_to_fy,
        'section_choices': ChartOfAccount.StatementSection.choices,
        'tax_code_choices': ['GST', 'FRE', 'ITS', 'ADS', 'CAP', 'INP', 'N-T', 'GNR'],
        'mapping_options': mapping_options,
        'code_ranges': [
            {'section': 'Revenue / Cost of Sales', 'range': '0 — 999'},
            {'section': 'Expenses', 'range': '1000 — 1999'},
            {'section': 'Current Assets', 'range': '2000 — 2499'},
            {'section': 'Non-Current Assets', 'range': '2500 — 2999'},
            {'section': 'Current Liabilities', 'range': '3000 — 3499'},
            {'section': 'Non-Current Liabilities', 'range': '3500 — 3999'},
            {'section': 'Equity', 'range': '4000+'},
        ],
    }
    return render(request, 'core/coa_form.html', context)


@login_required
def coa_edit(request, pk):
    """
    Edit an existing account in the Chart of Accounts.
    """
    account = get_object_or_404(ChartOfAccount, pk=pk)
    entity_type = account.entity_type
    return_to_fy = request.GET.get('fy', '')

    if request.method == 'POST':
        return_to_fy = request.POST.get('return_to_fy', '')
        account_code = request.POST.get('account_code', '').strip()
        account_name = request.POST.get('account_name', '').strip()
        section = request.POST.get('section', '')
        classification = request.POST.get('classification', '').strip()
        tax_code = request.POST.get('tax_code', '').strip()
        maps_to_id = request.POST.get('maps_to', '')

        errors = []
        if not account_code:
            errors.append('Account code is required.')
        if not account_name:
            errors.append('Account name is required.')
        if not section:
            errors.append('Section is required.')

        # Validate code range
        if account_code and section:
            valid, err = _validate_code_section(account_code, section)
            if not valid:
                errors.append(err)

        # Check uniqueness (exclude self)
        if account_code and ChartOfAccount.objects.filter(
            entity_type=entity_type, account_code=account_code
        ).exclude(pk=pk).exists():
            errors.append(f'Account code {account_code} already exists for this entity type.')

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            maps_to = None
            if maps_to_id:
                maps_to = AccountMapping.objects.filter(pk=maps_to_id).first()

            account.account_code = account_code
            account.account_name = account_name
            account.section = section
            account.classification = classification
            account.tax_code = tax_code
            account.maps_to = maps_to
            account.save()
            messages.success(request, f'Account {account_code} — {account_name} updated successfully.')
            if return_to_fy:
                return redirect(f"/years/{return_to_fy}/trial-balance/")
            return redirect(f"/chart-of-accounts/?entity_type={entity_type}")

    mapping_options = AccountMapping.objects.all().order_by('financial_statement', 'display_order')

    context = {
        'account': account,
        'entity_type': entity_type,
        'section_choices': ChartOfAccount.StatementSection.choices,
        'tax_code_choices': ['GST', 'FRE', 'ITS', 'ADS', 'CAP', 'INP', 'N-T', 'GNR'],
        'mapping_options': mapping_options,
        'code_ranges': [
            {'section': 'Revenue / Cost of Sales', 'range': '0 — 999'},
            {'section': 'Expenses', 'range': '1000 — 1999'},
            {'section': 'Current Assets', 'range': '2000 — 2499'},
            {'section': 'Non-Current Assets', 'range': '2500 — 2999'},
            {'section': 'Current Liabilities', 'range': '3000 — 3499'},
            {'section': 'Non-Current Liabilities', 'range': '3500 — 3999'},
            {'section': 'Equity', 'range': '4000+'},
        ],
        'is_edit': True,
        'return_to_fy': return_to_fy,
    }
    return render(request, 'core/coa_form.html', context)


@login_required
def coa_delete(request, pk):
    """
    Soft-delete an account (set is_active=False).
    """
    account = get_object_or_404(ChartOfAccount, pk=pk)
    entity_type = account.entity_type
    if request.method == 'POST':
        account.is_active = False
        account.save()
        messages.success(request, f'Account {account.account_code} — {account.account_name} has been deactivated.')
    return redirect(f"/chart-of-accounts/?entity_type={entity_type}")


@login_required
def coa_check_code(request):
    """
    AJAX endpoint to check if an account code is available and return the expected section.
    """
    code = request.GET.get('code', '').strip()
    entity_type = request.GET.get('entity_type', 'company')
    exclude_pk = request.GET.get('exclude', '')

    result = {'available': False, 'sections': [], 'error': ''}

    if not code:
        result['error'] = 'Enter an account code.'
        return JsonResponse(result)

    # Check availability
    qs = ChartOfAccount.objects.filter(entity_type=entity_type, account_code=code)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    if qs.exists():
        existing = qs.first()
        result['error'] = f'Code {code} is already used by "{existing.account_name}".'
        return JsonResponse(result)

    # Determine expected section
    sections = _section_for_code(code)
    if not sections:
        result['error'] = f'Code {code} does not fall within any known range.'
        return JsonResponse(result)

    result['available'] = True
    result['sections'] = [{'value': s[0], 'label': s[1]} for s in sections]
    return JsonResponse(result)


@login_required
def coa_suggest_code(request):
    """
    AJAX endpoint to suggest the next available account code.
    Given a section and account_name, finds the correct position
    alphabetically among existing accounts in that section and
    returns an available code.

    GET /chart-of-accounts/suggest-code/?section=expenses&account_name=Advertising&entity_type=company
    Returns: {"suggested_code": "1015", "position_info": "Between 1010 (Accounting Fees) and 1020 (Bank Charges)"}
    """
    section = request.GET.get('section', '').strip()
    account_name = request.GET.get('account_name', '').strip()
    entity_type = request.GET.get('entity_type', 'company')

    if not section or not account_name:
        return JsonResponse({'suggested_code': '', 'position_info': 'Enter a section and account name.'})

    # Get all active accounts in this section, ordered by account_name
    existing = list(
        ChartOfAccount.objects.filter(
            entity_type=entity_type, section=section, is_active=True
        ).order_by('account_name')
    )

    # Also handle related sections that share the same code range
    # e.g., revenue and cost_of_sales both use 0-999
    shared_sections = {
        'revenue': ['revenue', 'cost_of_sales'],
        'cost_of_sales': ['revenue', 'cost_of_sales'],
        'equity': ['equity', 'capital_accounts', 'pl_appropriation'],
        'capital_accounts': ['equity', 'capital_accounts', 'pl_appropriation'],
        'pl_appropriation': ['equity', 'capital_accounts', 'pl_appropriation'],
    }
    related = shared_sections.get(section, [section])

    # Get ALL accounts in the shared code range to avoid code collisions
    all_in_range = list(
        ChartOfAccount.objects.filter(
            entity_type=entity_type, section__in=related, is_active=True
        ).order_by('account_code')
    )
    used_codes = {int(a.account_code.split('.')[0]) for a in all_in_range if a.account_code.split('.')[0].isdigit()}

    # Determine the code range for this section
    # Use more specific sub-ranges where applicable
    sub_ranges = {
        'revenue':          (0, 999),
        'cost_of_sales':    (0, 999),
        'expenses':         (1000, 1999),
        'assets':           (2000, 2999),
        'liabilities':      (3000, 3999),
        'equity':           (4000, 4999),
        'capital_accounts': (4000, 4999),
        'pl_appropriation': (4000, 4999),
        'suspense':         (9000, 9999),
    }
    lo, hi = sub_ranges.get(section, (0, 9999))

    # Find where this account_name fits alphabetically
    name_lower = account_name.lower()
    before = None  # account immediately before alphabetically
    after = None   # account immediately after alphabetically

    for acc in existing:
        if acc.account_name.lower() < name_lower:
            before = acc
        elif acc.account_name.lower() > name_lower:
            after = acc
            break

    # Determine the target code range
    if before and after:
        # Insert between two existing accounts
        try:
            code_before = int(before.account_code.split('.')[0])
            code_after = int(after.account_code.split('.')[0])
        except ValueError:
            code_before = lo
            code_after = hi
        target_lo = code_before + 1
        target_hi = code_after - 1
        position_info = f'Between {before.account_code} ({before.account_name}) and {after.account_code} ({after.account_name})'
    elif before and not after:
        # Goes at the end of the section
        try:
            code_before = int(before.account_code.split('.')[0])
        except ValueError:
            code_before = lo
        target_lo = code_before + 1
        target_hi = hi
        position_info = f'After {before.account_code} ({before.account_name})'
    elif after and not before:
        # Goes at the start of the section
        try:
            code_after = int(after.account_code.split('.')[0])
        except ValueError:
            code_after = hi
        target_lo = lo
        target_hi = code_after - 1
        position_info = f'Before {after.account_code} ({after.account_name})'
    else:
        # No existing accounts in this section — use the section start
        target_lo = lo
        target_hi = hi
        position_info = 'First account in this section'

    # Find the first available code in the target range
    suggested_code = ''
    if target_lo <= target_hi:
        # Try to find a code in the middle of the range first (for future insertions)
        mid = (target_lo + target_hi) // 2
        # Try mid first, then scan outward
        if mid not in used_codes and lo <= mid <= hi:
            suggested_code = str(mid)
        else:
            # Scan from target_lo upward
            for c in range(target_lo, target_hi + 1):
                if c not in used_codes and lo <= c <= hi:
                    suggested_code = str(c)
                    break

    if not suggested_code:
        # No space in the ideal range — find any available code in the section
        for c in range(lo, hi + 1):
            if c not in used_codes:
                suggested_code = str(c)
                position_info += ' (no space in ideal range, using next available)'
                break

    if not suggested_code:
        return JsonResponse({
            'suggested_code': '',
            'position_info': 'No available codes in this section range. All codes are used.',
            'error': True,
        })

    # Pad code to match existing convention (e.g., 4-digit for 1000+)
    if int(suggested_code) >= 1000:
        suggested_code = suggested_code.zfill(4)

    return JsonResponse({
        'suggested_code': suggested_code,
        'position_info': position_info,
    })


# ---------------------------------------------------------------------------
# Audit Library
# ---------------------------------------------------------------------------
@login_required
def audit_library(request):
    """
    Display the audit risk rules library and reference data.
    Shows all configured risk rules with their categories, severity, and status.
    """
    category_filter = request.GET.get("category", "")
    severity_filter = request.GET.get("severity", "")
    query = request.GET.get("q", "")

    rules = RiskRule.objects.all()

    if query:
        rules = rules.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(rule_id__icontains=query)
        )

    if category_filter:
        rules = rules.filter(category=category_filter)

    if severity_filter:
        rules = rules.filter(severity=severity_filter)

    # Group rules by category
    grouped_rules = {}
    for rule in rules:
        cat = rule.get_category_display()
        if cat not in grouped_rules:
            grouped_rules[cat] = []
        grouped_rules[cat].append(rule)

    # Reference data
    reference_data = RiskReferenceData.objects.all()

    # Stats
    total_rules = RiskRule.objects.count()
    active_rules = RiskRule.objects.filter(is_active=True).count()
    total_open_flags = RiskFlag.objects.filter(status="open").count()

    context = {
        "grouped_rules": grouped_rules,
        "reference_data": reference_data,
        "total_rules": total_rules,
        "active_rules": active_rules,
        "total_open_flags": total_open_flags,
        "query": query,
        "category_filter": category_filter,
        "severity_filter": severity_filter,
        "category_choices": RiskRule.Category.choices,
        "severity_choices": RiskRule.Severity.choices,
    }
    return render(request, "core/audit_library.html", context)


# ---------------------------------------------------------------------------
# Run Risk Engine
# ---------------------------------------------------------------------------
@login_required
@require_POST
def run_risk_engine_view(request, pk):
    """
    Trigger the risk engine for a financial year.
    Runs Tier 1 (variance) and Tier 2 (compliance) analysis.
    """
    fy = get_financial_year_for_user(request, pk)

    if fy.is_locked:
        messages.warning(request, "Cannot run risk engine on a finalised financial year.")
        return redirect("core:financial_year_detail", pk=pk)

    try:
        from core.risk_engine import run_risk_engine
        results = run_risk_engine(fy, tiers=[1, 2])

        if results["errors"]:
            for err in results["errors"][:5]:
                messages.warning(request, f"Engine warning: {err}")

        flags_msg = f"Risk engine complete: {results['flags_created']} flag(s) raised"
        if results["flags_auto_resolved"] > 0:
            flags_msg += f", {results['flags_auto_resolved']} auto-resolved"

        messages.success(request, flags_msg)

    except Exception as e:
        logger.exception("Risk engine error")
        messages.error(request, f"Risk engine error: {str(e)}")

    return redirect("core:risk_flags", pk=pk)


# ---------------------------------------------------------------------------
# Risk Flags Detail (Enhanced Card-Based UI)
# ---------------------------------------------------------------------------
@login_required
def risk_flags_view(request, pk):
    """
    Show all risk flags for a specific financial year.
    Enhanced card-based layout with filtering by severity, status, tier, and category.
    """
    fy = get_financial_year_for_user(request, pk)
    flags = fy.risk_flags.all().order_by("-severity", "-created_at")

    # Filters
    severity_filter = request.GET.get("severity", "")
    status_filter = request.GET.get("status", "")
    tier_filter = request.GET.get("tier", "")
    category_filter = request.GET.get("category", "")

    if severity_filter:
        flags = flags.filter(severity=severity_filter)
    if status_filter:
        flags = flags.filter(status=status_filter)
    if tier_filter:
        flags = flags.filter(tier=int(tier_filter))
    if category_filter:
        # Match by rule category via rule_id prefix or lookup
        rule_ids = RiskRule.objects.filter(category=category_filter).values_list("rule_id", flat=True)
        flags = flags.filter(rule_id__in=rule_ids)

    # Summary counts (unfiltered)
    all_flags = fy.risk_flags.all()
    total_flags_count = all_flags.count()
    open_flags = all_flags.filter(status="open")
    total_open = open_flags.count()

    # Per-severity total counts
    critical_count = all_flags.filter(severity="CRITICAL").count()
    high_count = all_flags.filter(severity="HIGH").count()
    medium_count = all_flags.filter(severity="MEDIUM").count()
    low_count = all_flags.filter(severity="LOW").count()

    # Per-severity open counts
    critical_open = open_flags.filter(severity="CRITICAL").count()
    high_open = open_flags.filter(severity="HIGH").count()
    medium_open = open_flags.filter(severity="MEDIUM").count()
    low_open = open_flags.filter(severity="LOW").count()
    medium_low_open = medium_open + low_open

    # Per-severity resolved counts
    resolved_flags = all_flags.filter(status__in=["resolved", "auto_resolved"])
    resolved_count = resolved_flags.count()
    critical_resolved = resolved_flags.filter(severity="CRITICAL").count()
    high_resolved = resolved_flags.filter(severity="HIGH").count()
    medium_resolved = resolved_flags.filter(severity="MEDIUM").count()
    low_resolved = resolved_flags.filter(severity="LOW").count()

    reviewed_count = all_flags.filter(status="reviewed").count()

    # Progress percentages
    resolution_pct = round((resolved_count / total_flags_count) * 100) if total_flags_count > 0 else 0
    open_pct = 100 - resolution_pct

    # Tier breakdown
    tier1_count = all_flags.filter(tier=1).count()
    tier2_count = all_flags.filter(tier=2).count()
    tier3_count = all_flags.filter(tier=3).count()

    # Category breakdown for filter
    categories_in_use = set()
    for flag in all_flags:
        rule = RiskRule.objects.filter(rule_id=flag.rule_id).first()
        if rule:
            categories_in_use.add((rule.category, rule.get_category_display()))

    # Annotate flags with AI data if available
    flag_list = []
    for flag in flags:
        flag_dict = {
            "flag": flag,
            "ai_explanation": getattr(flag, "ai_explanation", "") or "",
            "ai_suggested_action": getattr(flag, "ai_suggested_action", "") or "",
            "ato_interest_score": getattr(flag, "ato_interest_score", None),
            "has_ai": bool(getattr(flag, "ai_explanation", "")),
        }
        flag_list.append(flag_dict)

    context = {
        "financial_year": fy,
        "entity": fy.entity,
        "flags": flag_list,
        "total_flags": total_flags_count,
        "total_open": total_open,
        # Per-severity totals
        "critical_count": critical_count,
        "high_count": high_count,
        "medium_count": medium_count,
        "low_count": low_count,
        # Per-severity open
        "critical_open": critical_open,
        "high_open": high_open,
        "medium_open": medium_open,
        "low_open": low_open,
        "medium_low_open": medium_low_open,
        # Per-severity resolved
        "resolved_count": resolved_count,
        "critical_resolved": critical_resolved,
        "high_resolved": high_resolved,
        "medium_resolved": medium_resolved,
        "low_resolved": low_resolved,
        "reviewed_count": reviewed_count,
        # Progress
        "resolution_pct": resolution_pct,
        "open_pct": open_pct,
        # Tier breakdown
        "tier1_count": tier1_count,
        "tier2_count": tier2_count,
        "tier3_count": tier3_count,
        "categories_in_use": sorted(categories_in_use, key=lambda x: x[1]),
        # Current filters
        "severity_filter": severity_filter,
        "status_filter": status_filter,
        "tier_filter": tier_filter,
        "category_filter": category_filter,
    }
    return render(request, "core/risk_flags.html", context)


# ---------------------------------------------------------------------------
# Resolve Risk Flag
# ---------------------------------------------------------------------------
@login_required
@require_POST
def resolve_risk_flag(request, pk):
    """Resolve a single risk flag. Requires minimum 5-word resolution notes."""
    flag = get_object_or_404(RiskFlag, pk=pk)
    resolution_notes = request.POST.get("resolution_notes", "").strip()
    new_status = request.POST.get("new_status", "resolved")

    if new_status == "resolved":
        word_count = len(resolution_notes.split())
        if word_count < 5:
            messages.error(
                request,
                f"Resolution notes must contain at least 5 words (you wrote {word_count}). "
                f"Please describe how this risk was addressed."
            )
            return redirect("core:risk_flags", pk=flag.financial_year.pk)

        flag.status = "resolved"
        flag.resolution_notes = resolution_notes
        flag.resolved_by = request.user
        flag.resolved_at = timezone.now()
        flag.save()
        messages.success(request, f"Risk flag resolved: {flag.title}")

    elif new_status == "reviewed":
        flag.status = "reviewed"
        if resolution_notes:
            flag.resolution_notes = resolution_notes
        flag.save()
        messages.info(request, f"Risk flag marked as reviewed: {flag.title}")

    return redirect("core:risk_flags", pk=flag.financial_year.pk)


# ---------------------------------------------------------------------------
# AI Risk Analysis (Tier 3) — Single Flag
# ---------------------------------------------------------------------------
@login_required
@require_POST
def ai_analyse_flag(request, pk):
    """
    Run AI contextual analysis on a single risk flag.
    Calls Claude API to generate a plain-English explanation and suggested action.
    """
    flag = get_object_or_404(RiskFlag, pk=pk)

    try:
        from core.ai_service import analyse_risk_flag
        result = analyse_risk_flag(flag)

        if result.get("success"):
            flag.ai_explanation = result["explanation"]
            flag.ai_suggested_action = result["suggested_action"]
            flag.ai_data_hash = result.get("data_hash", "")
            flag.save()
            messages.success(request, f"AI analysis complete for: {flag.title}")
        else:
            messages.warning(request, f"AI analysis returned no result: {result.get('error', 'Unknown')}")

    except Exception as e:
        logger.exception("AI analysis error")
        messages.error(request, f"AI analysis error: {str(e)}")

    return redirect("core:risk_flags", pk=flag.financial_year.pk)


# ---------------------------------------------------------------------------
# AI Batch Analysis — All Flags for FY (uses batch_analyse_flags)
# ---------------------------------------------------------------------------
@login_required
@require_POST
def ai_analyse_all_flags(request, pk):
    """
    Run batch AI analysis on all open flags for a financial year.
    Uses the batch engine with caching and materiality-aware prompts.
    """
    fy = get_financial_year_for_user(request, pk)
    force = request.POST.get("force", "") == "1"

    try:
        from core.ai_service import batch_analyse_flags
        result = batch_analyse_flags(fy, force=force)

        if result.get("success"):
            msg = f"AI batch analysis complete: {result['analysed']} analysed"
            if result.get('skipped'):
                msg += f", {result['skipped']} cached (skipped)"
            if result.get('errors'):
                msg += f", {result['errors']} error(s)"
            messages.success(request, msg)
        else:
            messages.warning(request, "Batch analysis returned no results.")

    except Exception as e:
        logger.exception("AI batch analysis error")
        messages.error(request, f"AI batch analysis error: {str(e)}")

    return redirect("core:risk_flags", pk=pk)


# ---------------------------------------------------------------------------
# AI Feedback — Record user corrections on AI analysis
# ---------------------------------------------------------------------------
@login_required
@require_POST
def ai_feedback_view(request, pk):
    """
    Record user feedback on an AI analysis result.
    Stores the feedback for future prompt improvement.
    """
    flag = get_object_or_404(RiskFlag, pk=pk)
    feedback_type = request.POST.get("feedback_type", "")
    user_notes = request.POST.get("feedback_notes", "").strip()

    valid_types = ["correct", "partially_correct", "incorrect", "irrelevant"]
    if feedback_type not in valid_types:
        messages.error(request, "Invalid feedback type.")
        return redirect("core:risk_flags", pk=flag.financial_year.pk)

    try:
        from core.ai_service import record_feedback
        record_feedback(flag, feedback_type, user_notes, request.user)
        messages.success(request, f"Feedback recorded for: {flag.title}")
    except Exception as e:
        logger.exception("AI feedback error")
        messages.error(request, f"Error recording feedback: {str(e)}")

    return redirect("core:risk_flags", pk=flag.financial_year.pk)


# ---------------------------------------------------------------------------
# AI Smart Flag Prioritisation
# ---------------------------------------------------------------------------
@login_required
@require_POST
def ai_prioritise_flags(request, pk):
    """
    Run AI prioritisation to score flags by 'Likely ATO Interest'.
    """
    fy = get_financial_year_for_user(request, pk)
    flags = fy.risk_flags.filter(status__in=["open", "reviewed"])

    try:
        from core.ai_service import prioritise_flags
        result = prioritise_flags(list(flags), fy)

        if result.get("success"):
            scored = result.get("scored", 0)
            messages.success(request, f"AI prioritisation complete: {scored} flag(s) scored.")
        else:
            messages.warning(request, f"Prioritisation issue: {result.get('error', 'Unknown')}")

    except Exception as e:
        logger.exception("AI prioritisation error")
        messages.error(request, f"AI prioritisation error: {str(e)}")

    return redirect("core:risk_flags", pk=pk)


# ---------------------------------------------------------------------------
# Generate AI Risk Summary Report
# ---------------------------------------------------------------------------
@login_required
def generate_risk_report(request, pk):
    """
    Generate a narrative AI Risk Summary Report in Word format.
    """
    fy = get_financial_year_for_user(request, pk)

    try:
        from core.ai_service import generate_risk_summary_report
        doc_bytes = generate_risk_summary_report(fy)

        response = HttpResponse(
            doc_bytes,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        filename = f"Risk_Summary_{fy.entity.entity_name}_{fy.year_label}.docx"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    except Exception as e:
        logger.exception("Risk report generation error")
        messages.error(request, f"Report generation error: {str(e)}")
        return redirect("core:risk_flags", pk=pk)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _compute_flag_hash(flag):
    """Compute a hash of the flag's data for AI cache invalidation."""
    data_str = json.dumps({
        "rule_id": flag.rule_id,
        "severity": flag.severity,
        "description": flag.description,
        "calculated_values": flag.calculated_values,
        "affected_accounts": flag.affected_accounts,
    }, sort_keys=True)
    return hashlib.sha256(data_str.encode()).hexdigest()
