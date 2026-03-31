"""
StatementHub Risk Engine
========================
Three-tier risk analysis engine for financial year data.

Tier 1: Automated Variance Analysis (mathematical)
Tier 2a: Dedicated Detection Modules (Div 7A, Going Concern, S100A, RP, SGC, TPAR)
Tier 2b: Individual Rule-Based ATO Compliance (remaining configurable rules)
Tier 3: AI Contextual Risk Analysis (Claude API — see ai_service.py)

Module Architecture:
    Dedicated modules (core.risk_modules) handle complex multi-rule compliance
    areas.  Each module produces a single consolidated assessment + EvaFinding.
    Individual Tier 2 rules that are covered by a module are automatically
    skipped via MODULE_COVERS in core.risk_modules.registry.

Usage:
    from core.risk_engine import run_risk_engine
    results = run_risk_engine(financial_year)
"""

import uuid
import logging
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from django.utils import timezone
from django.db.models import Sum, Q

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")


def _derive_section(mapped_line_item):
    """Derive a risk-engine section category from an AccountMapping object.

    AccountMapping has:
      - financial_statement: income_statement | balance_sheet | equity | cash_flow
      - statement_section: free text like 'Current Assets', 'Operating Revenue'

    We map these to the categories the risk engine uses:
      revenue, cost_of_sales, expenses, assets, liabilities, equity,
      capital_accounts, pl_appropriation
    """
    if mapped_line_item is None:
        return ""

    fs = getattr(mapped_line_item, 'financial_statement', '')
    ss = (getattr(mapped_line_item, 'statement_section', '') or '').lower()

    # Balance sheet sections
    if fs == 'balance_sheet':
        if 'asset' in ss:
            return 'assets'
        elif 'liabilit' in ss:
            return 'liabilities'
        else:
            return 'assets'  # default for balance sheet

    # Equity statement
    if fs == 'equity':
        return 'equity'

    # Income statement — derive from statement_section text
    if fs == 'income_statement':
        if 'revenue' in ss or 'income' in ss or 'sales' in ss or 'turnover' in ss:
            return 'revenue'
        elif 'cost of' in ss or 'cost_of' in ss or 'cogs' in ss:
            return 'cost_of_sales'
        else:
            return 'expenses'  # default for income statement items

    # Cash flow — not typically used for risk variance
    if fs == 'cash_flow':
        return ''

    return ''


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def run_risk_engine(financial_year, tiers=None):
    """
    Run the risk engine against a financial year.

    Args:
        financial_year: FinancialYear instance
        tiers: list of tier numbers to run (default: [1, 2]). Tier 3 is AI
               and runs separately via ai_service.

    Returns:
        dict with 'run_id', 'flags_created', 'flags_auto_resolved', 'errors'
    """
    from core.models import RiskFlag, RiskRule, RiskReferenceData

    if tiers is None:
        tiers = [1, 2]

    run_id = uuid.uuid4()
    results = {
        "run_id": str(run_id),
        "flags_created": 0,
        "flags_auto_resolved": 0,
        "errors": [],
    }

    # Load trial balance data
    tb_data = _load_trial_balance(financial_year)
    if not tb_data["lines"]:
        results["errors"].append("No trial balance lines found.")
        return results

    # Load reference data
    ref_data = _load_reference_data(financial_year.year_label)

    # Entity context
    entity = financial_year.entity
    entity_context = {
        "entity_type": entity.entity_type,
        "entity_name": entity.entity_name,
        "abn": entity.abn,
        "reporting_framework": entity.reporting_framework,
        "company_size": getattr(entity, "company_size", ""),
        "year_label": financial_year.year_label,
        "start_date": str(financial_year.start_date),
        "end_date": str(financial_year.end_date),
    }

    # Track which rule_ids still trigger, per tier
    new_rule_ids_by_tier = {1: set(), 2: set()}

    # --- TIER 1: Variance Analysis ---
    if 1 in tiers:
        tier1_flags = _run_tier1_variance(
            financial_year, tb_data, ref_data, entity_context, run_id
        )
        for flag_data in tier1_flags:
            _create_flag(financial_year, run_id, flag_data)
            results["flags_created"] += 1
            new_rule_ids_by_tier[1].add(flag_data["rule_id"])

    # --- TIER 2a: Dedicated Detection Modules ---
    # Modules run first and produce consolidated assessment records +
    # EvaFinding cards.  They replace individual rules listed in
    # MODULE_COVERS (see core.risk_modules.registry).
    module_results = []
    if 2 in tiers:
        from core.risk_modules.registry import get_module_classes, is_covered_by_module

        for ModuleClass in get_module_classes():
            try:
                module = ModuleClass(financial_year)
                assessment = module.run()
                if assessment is not None:
                    module_results.append({
                        "module_id": module.module_id,
                        "severity": module.overall_severity,
                        "rules_fired": module.rules_fired,
                    })
                    logger.info(
                        "Module %s: %s — %s (rules: %s)",
                        module.module_id, entity.entity_name,
                        module.overall_severity, module.rules_fired,
                    )
            except Exception as e:
                results["errors"].append(f"Module {ModuleClass.module_id}: {str(e)}")
                logger.exception(f"Error running module {ModuleClass.module_id}")

        results["module_results"] = module_results

    # --- TIER 2b: Individual Rule-Based Compliance ---
    # Skip rules that are now covered by dedicated modules.
    if 2 in tiers:
        from core.risk_modules.registry import is_covered_by_module

        active_rules = RiskRule.objects.filter(
            is_active=True, tier=2
        )
        for rule in active_rules:
            # Skip rules covered by a dedicated module
            if is_covered_by_module(rule.rule_id):
                continue
            # Check if rule applies to this entity type
            if rule.applicable_entities and entity.entity_type not in rule.applicable_entities:
                continue
            try:
                flag_data = _evaluate_tier2_rule(
                    rule, financial_year, tb_data, ref_data, entity_context
                )
                if flag_data:
                    _create_flag(financial_year, run_id, flag_data)
                    results["flags_created"] += 1
                    new_rule_ids_by_tier[2].add(flag_data["rule_id"])
            except Exception as e:
                results["errors"].append(f"Rule {rule.rule_id}: {str(e)}")
                logger.exception(f"Error evaluating rule {rule.rule_id}")

    # Auto-resolve: ONLY resolve flags from tiers that were actually
    # evaluated in this run.  This prevents Tier 2 flags from being
    # wiped when only Tier 1 runs (and vice versa).
    auto_resolved = 0
    for tier_num in tiers:
        previous_open_for_tier = RiskFlag.objects.filter(
            financial_year=financial_year,
            status__in=["open", "reviewed"],
            tier=tier_num,
        )
        stale = previous_open_for_tier.exclude(
            rule_id__in=new_rule_ids_by_tier.get(tier_num, set())
        )
        auto_resolved += stale.update(
            status="auto_resolved",
            resolution_notes="Auto-resolved: condition no longer detected by risk engine.",
            resolved_at=timezone.now(),
        )
    results["flags_auto_resolved"] = auto_resolved

    return results


# ============================================================================
# DATA LOADING
# ============================================================================

def _load_trial_balance(financial_year):
    """Load and structure trial balance data for analysis.

    IMPORTANT: This function AGGREGATES multiple TrialBalanceLine records
    per account code into a single virtual line.  This mirrors the view's
    display logic and prevents false positives in the risk engine (e.g.
    "new account, no comparatives" when the comparative data lives on a
    separate rollover line for the same account code).

    For rolled-forward balance-sheet items and some Xero imports,
    debit/credit may both be zero while closing_balance holds the real value.
    We compute effective_dr / effective_cr (mirroring the view's display_dr /
    display_cr logic) so that variance analysis and Tier 2 rules see the
    correct balances.
    """
    from core.models import TrialBalanceLine

    raw_lines = TrialBalanceLine.objects.filter(
        financial_year=financial_year
    ).select_related("mapped_line_item")

    # ---- Step 1: Aggregate raw lines by account_code ----
    # Group all lines by account_code, summing current and prior balances.
    code_groups = {}  # account_code -> list of lines
    for line in raw_lines:
        code_groups.setdefault(line.account_code, []).append(line)

    aggregated_lines = []
    for code, group in code_groups.items():
        # Sum raw current-year values
        total_debit = sum(l.debit or ZERO for l in group)
        total_credit = sum(l.credit or ZERO for l in group)
        total_closing = sum(l.closing_balance or ZERO for l in group)
        total_prior_dr = sum(l.prior_debit or ZERO for l in group)
        total_prior_cr = sum(l.prior_credit or ZERO for l in group)

        # Use the first line with a mapped_line_item as the representative
        representative = group[0]
        for l in group:
            if l.mapped_line_item:
                representative = l
                break

        # Build a virtual aggregated line (reuse the representative object)
        agg = representative
        agg.account_code = code
        agg.account_name = representative.account_name

        # Compute effective debit/credit from aggregated values
        if total_debit == 0 and total_credit == 0 and total_closing != 0:
            if total_closing > 0:
                agg.effective_dr = total_closing
                agg.effective_cr = ZERO
            else:
                agg.effective_dr = ZERO
                agg.effective_cr = abs(total_closing)
        else:
            # Net the debits and credits (adjustments reduce originals)
            net = total_debit - total_credit
            if net >= 0:
                agg.effective_dr = net
                agg.effective_cr = ZERO
            else:
                agg.effective_dr = ZERO
                agg.effective_cr = abs(net)

        # Set aggregated prior values on the line object
        agg.prior_debit = total_prior_dr
        agg.prior_credit = total_prior_cr

        aggregated_lines.append(agg)

    # ---- Step 2: Build the data structure from aggregated lines ----
    data = {
        "lines": [],
        "by_code": {},
        "by_section": {},
        "totals": {
            "revenue": ZERO,
            "cost_of_sales": ZERO,
            "expenses": ZERO,
            "assets": ZERO,
            "liabilities": ZERO,
            "equity": ZERO,
        },
        "prior_totals": {
            "revenue": ZERO,
            "cost_of_sales": ZERO,
            "expenses": ZERO,
            "assets": ZERO,
            "liabilities": ZERO,
            "equity": ZERO,
        },
    }

    for line in aggregated_lines:
        data["lines"].append(line)
        data["by_code"][line.account_code] = line

        section = _derive_section(line.mapped_line_item)
        if section not in data["by_section"]:
            data["by_section"][section] = []
        data["by_section"][section].append(line)

        # Net balance using effective values
        net = line.effective_dr - line.effective_cr
        prior_net = line.prior_debit - line.prior_credit

        if section == "revenue":
            data["totals"]["revenue"] += net
            data["prior_totals"]["revenue"] += prior_net
        elif section == "cost_of_sales":
            data["totals"]["cost_of_sales"] += net
            data["prior_totals"]["cost_of_sales"] += prior_net
        elif section == "expenses":
            data["totals"]["expenses"] += net
            data["prior_totals"]["expenses"] += prior_net
        elif section == "assets":
            data["totals"]["assets"] += net
            data["prior_totals"]["assets"] += prior_net
        elif section == "liabilities":
            data["totals"]["liabilities"] += net
            data["prior_totals"]["liabilities"] += prior_net
        elif section in ("equity", "capital_accounts", "pl_appropriation"):
            data["totals"]["equity"] += net
            data["prior_totals"]["equity"] += prior_net

    # Derived totals
    data["totals"]["gross_profit"] = (
        data["totals"]["revenue"] + data["totals"]["cost_of_sales"]
    )
    data["totals"]["net_profit"] = (
        data["totals"]["gross_profit"] + data["totals"]["expenses"]
    )
    data["prior_totals"]["gross_profit"] = (
        data["prior_totals"]["revenue"] + data["prior_totals"]["cost_of_sales"]
    )
    data["prior_totals"]["net_profit"] = (
        data["prior_totals"]["gross_profit"] + data["prior_totals"]["expenses"]
    )

    return data


def _load_reference_data(year_label):
    """Load reference data into a dict keyed by key name."""
    from core.models import RiskReferenceData

    ref = {}
    for rd in RiskReferenceData.objects.filter(
        Q(applicable_fy=year_label) | Q(applicable_fy="")
    ):
        try:
            ref[rd.key] = Decimal(rd.value)
        except (InvalidOperation, ValueError):
            ref[rd.key] = rd.value
    return ref


# ============================================================================
# TIER 1: AUTOMATED VARIANCE ANALYSIS
# ============================================================================

def _run_tier1_variance(financial_year, tb_data, ref_data, entity_context, run_id):
    """
    Tier 1: Compare current year balances to prior year.
    Flag significant variances by $ amount and % change.
    """
    flags = []

    # Thresholds from reference data or defaults
    pct_threshold = ref_data.get("variance_pct_threshold", Decimal("20"))
    abs_threshold = ref_data.get("variance_abs_threshold", Decimal("5000"))
    revenue_pct_threshold = ref_data.get("revenue_variance_pct", Decimal("15"))
    expense_pct_threshold = ref_data.get("expense_variance_pct", Decimal("20"))

    # Accounts to exclude from variance analysis — these always have
    # large variances by nature and should never be flagged.
    _EXCLUDED_CODES = {"4199", "9999"}  # Retained profits, suspense
    _EXCLUDED_KEYWORDS = {
        "retained profit", "retained earning", "accumulated profit",
        "accumulated earning", "profit & loss appropriation",
        "current year earnings", "current earnings",
    }

    for line in tb_data["lines"]:
        current_net = line.effective_dr - line.effective_cr
        prior_net = line.prior_debit - line.prior_credit

        # Skip if both are zero
        if current_net == ZERO and prior_net == ZERO:
            continue

        # Skip retained profits and similar equity accounts that always
        # have large variances (they change by the net profit each year).
        if line.account_code in _EXCLUDED_CODES:
            continue
        acct_lower = (line.account_name or "").lower()
        if any(kw in acct_lower for kw in _EXCLUDED_KEYWORDS):
            continue
        # Also skip if mapped to an equity retained-profits standard code
        if line.mapped_line_item:
            std_code = getattr(line.mapped_line_item, 'code', '') or ''
            if std_code in ('EQ-RE-001', 'EQ-RE-002'):
                continue

        variance_dollar = current_net - prior_net
        abs_variance = abs(variance_dollar)

        # Calculate percentage variance
        if prior_net != ZERO:
            variance_pct = (variance_dollar / abs(prior_net) * Decimal("100")).quantize(
                Decimal("0.1"), rounding=ROUND_HALF_UP
            )
        elif current_net != ZERO:
            variance_pct = Decimal("100.0")  # New account
        else:
            variance_pct = ZERO

        abs_pct = abs(variance_pct)

        # Determine section-specific threshold
        section = _derive_section(line.mapped_line_item)

        if section == "revenue":
            threshold = revenue_pct_threshold
        elif section in ("expenses", "cost_of_sales"):
            threshold = expense_pct_threshold
        else:
            threshold = pct_threshold

        # Flag if variance exceeds both thresholds
        if abs_pct >= threshold and abs_variance >= abs_threshold:
            # Determine severity based on magnitude
            if abs_pct >= Decimal("100") or abs_variance >= Decimal("50000"):
                severity = "HIGH"
            elif abs_pct >= Decimal("50") or abs_variance >= Decimal("20000"):
                severity = "MEDIUM"
            else:
                severity = "LOW"

            # New accounts (prior == 0, current != 0) are NOT flagged —
            # they are normal business activity.  Only flag disappeared accounts.
            if prior_net == ZERO and current_net != ZERO:
                continue  # skip new accounts entirely
            # Account disappeared (had prior year, now zero)
            elif current_net == ZERO and prior_net != ZERO:
                title = f"Account closed: {line.account_name}"
                description = (
                    f"Account {line.account_code} ({line.account_name}) had a "
                    f"prior year balance of ${prior_net:,.2f} but is now zero. "
                    f"Investigate whether this is expected."
                )
                severity = "LOW"
            else:
                direction = "increased" if variance_dollar > 0 else "decreased"
                title = f"Significant variance: {line.account_name}"
                description = (
                    f"Account {line.account_code} ({line.account_name}) has "
                    f"{direction} by ${abs_variance:,.2f} ({abs_pct}%) from "
                    f"${prior_net:,.2f} to ${current_net:,.2f}."
                )

            flags.append({
                "rule_id": f"T1-VAR-{line.account_code}",
                "tier": 1,
                "severity": severity,
                "title": title,
                "description": description,
                "affected_accounts": [line.account_code],
                "calculated_values": {
                    "current_balance": str(current_net),
                    "prior_balance": str(prior_net),
                    "variance_dollar": str(variance_dollar),
                    "variance_pct": str(variance_pct),
                    "section": section,
                },
                "recommended_action": (
                    "Review the account movement and obtain supporting documentation. "
                    "Consider whether the variance is consistent with the entity's "
                    "operations and any known changes in circumstances."
                ),
                "legislation_ref": "AASB 101 - Presentation of Financial Statements",
            })

    # Aggregate-level variance flags
    flags.extend(_check_aggregate_variances(tb_data, ref_data))

    # Division 7A detection: flag loan accounts with DEBIT closing balances
    # (i.e. money owed BY a shareholder/director TO the company).
    # This applies to companies and trusts.
    if entity_context.get("entity_type") in ("company", "trust", ""):
        flags.extend(_check_div7a_loans(tb_data, entity_context))

    return flags


def _check_div7a_loans(tb_data, entity_context):
    """
    Division 7A detection: flag loan accounts that carry a DEBIT closing balance.

    A debit balance on a loan account (liabilities section) means the company
    has lent money to a shareholder/associate, which triggers Division 7A of
    the Income Tax Assessment Act 1936.  These must be documented as compliant
    loan agreements or treated as deemed dividends.

    Detection heuristics:
      1. Account code starts with "3" (common AU liability prefix) AND
         account name contains "loan" or "director" or "shareholder".
      2. Account is mapped to a liabilities section AND name contains loan-
         related keywords.
    """
    flags = []
    _LOAN_KEYWORDS = {
        "loan", "director", "shareholder", "related party",
        "beneficiary", "associate", "advance",
    }

    for line in tb_data["lines"]:
        net = line.effective_dr - line.effective_cr
        # Only DEBIT (positive net) balances trigger Div 7A.  Zero balances
        # (loan fully repaid) and credit balances (company owes the person)
        # are NOT Div 7A exposures and must be skipped to avoid false positives.
        if net <= ZERO:
            continue  # credit or zero — normal for a liability

        name_lower = (line.account_name or "").lower()
        code = line.account_code or ""

        # Determine if this looks like a loan account
        is_loan = False

        # Heuristic 1: code starts with "3" (liabilities) + loan keyword
        if code.startswith("3") and any(kw in name_lower for kw in _LOAN_KEYWORDS):
            is_loan = True

        # Heuristic 2: mapped to liabilities section + loan keyword
        if not is_loan and line.mapped_line_item:
            section = _derive_section(line.mapped_line_item)
            mapped_code = getattr(line.mapped_line_item, 'code', '') or ''
            if (section == "liabilities" or mapped_code.startswith("BS-LIA")):
                if any(kw in name_lower for kw in _LOAN_KEYWORDS):
                    is_loan = True

        if is_loan:
            flags.append({
                "rule_id": f"T1-DIV7A-{code}",
                "tier": 1,
                "severity": "CRITICAL",
                "title": f"Div 7A: {line.account_name} has debit balance",
                "description": (
                    f"Account {code} ({line.account_name}) has a debit balance "
                    f"of ${net:,.2f}. A debit balance on a loan account indicates "
                    f"the company has lent money to a shareholder or associate. "
                    f"This triggers Division 7A of the ITAA 1936 and must be "
                    f"documented under a compliant loan agreement or treated as "
                    f"a deemed unfranked dividend."
                ),
                "affected_accounts": [code],
                "calculated_values": {
                    "account_code": code,
                    "account_name": line.account_name,
                    "debit_balance": str(net),
                },
                "recommended_action": (
                    "1. Confirm whether a Division 7A compliant loan agreement is in place. "
                    "2. Verify the loan meets minimum repayment and benchmark interest rate requirements. "
                    "3. If no compliant agreement exists, treat the amount as a deemed unfranked dividend "
                    "in the shareholder's tax return. "
                    "4. Document the analysis in the workpapers."
                ),
                "legislation_ref": "Division 7A, ITAA 1936 (ss 109C-109Q)",
            })

    return flags


def _check_aggregate_variances(tb_data, ref_data):
    """Check aggregate-level variances (total revenue, profit margin, etc.)."""
    flags = []
    totals = tb_data["totals"]
    prior = tb_data["prior_totals"]

    # Revenue change
    if prior["revenue"] != ZERO:
        rev_change_pct = (
            (totals["revenue"] - prior["revenue"]) / abs(prior["revenue"]) * 100
        ).quantize(Decimal("0.1"))
        if abs(rev_change_pct) >= Decimal("25"):
            flags.append({
                "rule_id": "T1-AGG-REV",
                "tier": 1,
                "severity": "MEDIUM",
                "title": "Significant revenue change",
                "description": (
                    f"Total revenue has changed by {rev_change_pct}% from "
                    f"${prior['revenue']:,.2f} to ${totals['revenue']:,.2f}. "
                    f"Investigate the cause of this significant movement."
                ),
                "affected_accounts": [],
                "calculated_values": {
                    "current_revenue": str(totals["revenue"]),
                    "prior_revenue": str(prior["revenue"]),
                    "change_pct": str(rev_change_pct),
                },
                "recommended_action": "Analyse revenue streams and identify the driver of the change.",
                "legislation_ref": "",
            })

    # Gross profit margin change
    if totals["revenue"] != ZERO and prior["revenue"] != ZERO:
        current_gp_margin = (totals["gross_profit"] / abs(totals["revenue"]) * 100).quantize(Decimal("0.1"))
        prior_gp_margin = (prior["gross_profit"] / abs(prior["revenue"]) * 100).quantize(Decimal("0.1"))
        margin_change = current_gp_margin - prior_gp_margin
        if abs(margin_change) >= Decimal("10"):
            flags.append({
                "rule_id": "T1-AGG-GPM",
                "tier": 1,
                "severity": "MEDIUM",
                "title": "Gross profit margin shift",
                "description": (
                    f"Gross profit margin has moved from {prior_gp_margin}% to "
                    f"{current_gp_margin}% (change of {margin_change} percentage points)."
                ),
                "affected_accounts": [],
                "calculated_values": {
                    "current_margin": str(current_gp_margin),
                    "prior_margin": str(prior_gp_margin),
                    "change": str(margin_change),
                },
                "recommended_action": "Investigate changes in cost structure or pricing.",
                "legislation_ref": "",
            })

    # Net profit change
    if prior["net_profit"] != ZERO:
        np_change_pct = (
            (totals["net_profit"] - prior["net_profit"]) / abs(prior["net_profit"]) * 100
        ).quantize(Decimal("0.1"))
        if abs(np_change_pct) >= Decimal("30"):
            flags.append({
                "rule_id": "T1-AGG-NP",
                "tier": 1,
                "severity": "MEDIUM" if abs(np_change_pct) < 50 else "HIGH",
                "title": "Significant net profit change",
                "description": (
                    f"Net profit has changed by {np_change_pct}% from "
                    f"${prior['net_profit']:,.2f} to ${totals['net_profit']:,.2f}."
                ),
                "affected_accounts": [],
                "calculated_values": {
                    "current_net_profit": str(totals["net_profit"]),
                    "prior_net_profit": str(prior["net_profit"]),
                    "change_pct": str(np_change_pct),
                },
                "recommended_action": "Review the key drivers of the profit change.",
                "legislation_ref": "",
            })

    return flags


# ============================================================================
# TIER 2: RULE-BASED ATO COMPLIANCE
# ============================================================================

def _evaluate_tier2_rule(rule, financial_year, tb_data, ref_data, entity_context):
    """
    Evaluate a single Tier 2 rule against the financial year data.

    Each rule's trigger_config contains:
        - "type": the rule evaluation type
        - additional parameters specific to the type

    Returns a flag dict if the rule triggers, or None.
    """
    config = rule.trigger_config or {}
    rule_type = config.get("type", "")

    evaluator = TIER2_EVALUATORS.get(rule_type)
    if evaluator:
        return evaluator(rule, financial_year, tb_data, ref_data, entity_context, config)
    else:
        # Generic account-based check
        return _eval_account_threshold(rule, financial_year, tb_data, ref_data, entity_context, config)


def _eval_account_threshold(rule, fy, tb, ref, ctx, config):
    """Check if specific accounts exceed thresholds."""
    account_codes = config.get("account_codes", [])
    account_keywords = config.get("account_keywords", [])
    threshold_key = config.get("threshold_key", "")
    threshold_value = config.get("threshold_value", 0)
    comparison = config.get("comparison", "gt")  # gt, lt, eq, ne, abs_gt

    # Get threshold from reference data or config
    threshold = ref.get(threshold_key, Decimal(str(threshold_value)))
    if not isinstance(threshold, Decimal):
        try:
            threshold = Decimal(str(threshold))
        except (InvalidOperation, ValueError):
            return None

    # Find matching accounts
    matched_lines = []
    for line in tb["lines"]:
        if line.account_code in account_codes:
            matched_lines.append(line)
        elif account_keywords:
            name_lower = line.account_name.lower()
            if any(kw.lower() in name_lower for kw in account_keywords):
                matched_lines.append(line)

    if not matched_lines:
        return None

    total = sum(line.effective_dr - line.effective_cr for line in matched_lines)

    triggered = False
    if comparison == "gt" and total > threshold:
        triggered = True
    elif comparison == "lt" and total < threshold:
        triggered = True
    elif comparison == "abs_gt" and abs(total) > threshold:
        triggered = True
    elif comparison == "eq" and total == threshold:
        triggered = True
    elif comparison == "ne" and total != threshold:
        triggered = True

    if triggered:
        return {
            "rule_id": rule.rule_id,
            "tier": 2,
            "severity": rule.severity,
            "title": rule.title,
            "description": rule.description.format(
                total=f"${total:,.2f}",
                threshold=f"${threshold:,.2f}",
                entity_name=ctx.get("entity_name", ""),
                year_label=ctx.get("year_label", ""),
            ),
            "affected_accounts": [l.account_code for l in matched_lines],
            "calculated_values": {
                "total": str(total),
                "threshold": str(threshold),
                "accounts": [
                    {"code": l.account_code, "name": l.account_name, "net": str(l.debit - l.credit)}
                    for l in matched_lines
                ],
            },
            "recommended_action": rule.recommended_action,
            "legislation_ref": rule.legislation_ref,
        }
    return None


def _eval_ratio_check(rule, fy, tb, ref, ctx, config):
    """Check financial ratios against thresholds."""
    numerator_codes = config.get("numerator_codes", [])
    numerator_keywords = config.get("numerator_keywords", [])
    denominator_codes = config.get("denominator_codes", [])
    denominator_keywords = config.get("denominator_keywords", [])
    denominator_total = config.get("denominator_total", "")  # e.g. "revenue"
    threshold = Decimal(str(config.get("threshold_value", 0)))
    comparison = config.get("comparison", "gt")

    num = ZERO
    den = ZERO

    for line in tb["lines"]:
        code = line.account_code
        name_lower = line.account_name.lower()
        net = line.effective_dr - line.effective_cr

        if code in numerator_codes or any(kw.lower() in name_lower for kw in numerator_keywords):
            num += net
        if code in denominator_codes or any(kw.lower() in name_lower for kw in denominator_keywords):
            den += net

    if denominator_total and denominator_total in tb["totals"]:
        den = abs(tb["totals"][denominator_total])

    if den == ZERO:
        return None

    ratio = (num / den * Decimal("100")).quantize(Decimal("0.1"))

    triggered = False
    if comparison == "gt" and ratio > threshold:
        triggered = True
    elif comparison == "lt" and ratio < threshold:
        triggered = True

    if triggered:
        return {
            "rule_id": rule.rule_id,
            "tier": 2,
            "severity": rule.severity,
            "title": rule.title,
            "description": rule.description.format(
                ratio=f"{ratio}%",
                threshold=f"{threshold}%",
                numerator=f"${num:,.2f}",
                denominator=f"${den:,.2f}",
                entity_name=ctx.get("entity_name", ""),
            ),
            "affected_accounts": numerator_codes[:5],
            "calculated_values": {
                "ratio": str(ratio),
                "threshold": str(threshold),
                "numerator": str(num),
                "denominator": str(den),
            },
            "recommended_action": rule.recommended_action,
            "legislation_ref": rule.legislation_ref,
        }
    return None


def _eval_balance_sign(rule, fy, tb, ref, ctx, config):
    """Check if accounts have unexpected debit/credit signs."""
    account_keywords = config.get("account_keywords", [])
    expected_sign = config.get("expected_sign", "credit")  # "debit" or "credit"

    flagged = []
    for line in tb["lines"]:
        name_lower = line.account_name.lower()
        if any(kw.lower() in name_lower for kw in account_keywords):
            net = line.effective_dr - line.effective_cr
            if expected_sign == "credit" and net > ZERO:
                flagged.append(line)
            elif expected_sign == "debit" and net < ZERO:
                flagged.append(line)

    if flagged:
        return {
            "rule_id": rule.rule_id,
            "tier": 2,
            "severity": rule.severity,
            "title": rule.title,
            "description": rule.description.format(
                count=len(flagged),
                entity_name=ctx.get("entity_name", ""),
            ),
            "affected_accounts": [l.account_code for l in flagged],
            "calculated_values": {
                "accounts": [
                    {"code": l.account_code, "name": l.account_name, "net": str(l.debit - l.credit)}
                    for l in flagged
                ],
            },
            "recommended_action": rule.recommended_action,
            "legislation_ref": rule.legislation_ref,
        }
    return None


def _eval_solvency(rule, fy, tb, ref, ctx, config):
    """Check solvency indicators."""
    current_assets = ZERO
    current_liabilities = ZERO
    total_assets = ZERO
    total_liabilities = ZERO

    for line in tb["lines"]:
        if not line.mapped_line_item:
            continue
        section = _derive_section(line.mapped_line_item)
        net = line.effective_dr - line.effective_cr
        code_lower = line.account_name.lower()

        if section == "assets":
            total_assets += net
            # Heuristic: current assets typically have these keywords
            if any(kw in code_lower for kw in ["cash", "bank", "receivable", "inventory",
                                                 "stock", "prepaid", "current", "trade debtor"]):
                current_assets += net
        elif section == "liabilities":
            total_liabilities += abs(net)
            if any(kw in code_lower for kw in ["payable", "creditor", "gst", "payg",
                                                 "provision", "accrued", "current", "overdraft"]):
                current_liabilities += abs(net)

    check_type = config.get("check_type", "current_ratio")

    if check_type == "current_ratio" and current_liabilities > ZERO:
        ratio = (current_assets / current_liabilities).quantize(Decimal("0.01"))
        if ratio < Decimal(str(config.get("threshold_value", "1.0"))):
            return {
                "rule_id": rule.rule_id,
                "tier": 2,
                "severity": rule.severity,
                "title": rule.title,
                "description": rule.description.format(
                    ratio=str(ratio),
                    current_assets=f"${current_assets:,.2f}",
                    current_liabilities=f"${current_liabilities:,.2f}",
                    entity_name=ctx.get("entity_name", ""),
                ),
                "affected_accounts": [],
                "calculated_values": {
                    "current_ratio": str(ratio),
                    "current_assets": str(current_assets),
                    "current_liabilities": str(current_liabilities),
                },
                "recommended_action": rule.recommended_action,
                "legislation_ref": rule.legislation_ref,
            }

    elif check_type == "net_assets":
        net_assets = total_assets - total_liabilities
        if net_assets < ZERO:
            return {
                "rule_id": rule.rule_id,
                "tier": 2,
                "severity": rule.severity,
                "title": rule.title,
                "description": rule.description.format(
                    net_assets=f"${net_assets:,.2f}",
                    total_assets=f"${total_assets:,.2f}",
                    total_liabilities=f"${total_liabilities:,.2f}",
                    entity_name=ctx.get("entity_name", ""),
                ),
                "affected_accounts": [],
                "calculated_values": {
                    "net_assets": str(net_assets),
                    "total_assets": str(total_assets),
                    "total_liabilities": str(total_liabilities),
                },
                "recommended_action": rule.recommended_action,
                "legislation_ref": rule.legislation_ref,
            }

    return None


def _eval_loan_check(rule, fy, tb, ref, ctx, config):
    """Check loan accounts for Division 7A and related party issues."""
    account_keywords = config.get("account_keywords", [])
    check_type = config.get("check_type", "div7a_loan")

    flagged = []
    for line in tb["lines"]:
        name_lower = line.account_name.lower()
        if any(kw.lower() in name_lower for kw in account_keywords):
            net = line.effective_dr - line.effective_cr
            if net > ZERO:  # Debit balance = amount owed TO the company
                flagged.append(line)

    if not flagged:
        return None

    total_loans = sum(l.debit - l.credit for l in flagged)

    return {
        "rule_id": rule.rule_id,
        "tier": 2,
        "severity": rule.severity,
        "title": rule.title,
        "description": rule.description.format(
            total=f"${total_loans:,.2f}",
            count=len(flagged),
            entity_name=ctx.get("entity_name", ""),
        ),
        "affected_accounts": [l.account_code for l in flagged],
        "calculated_values": {
            "total_loans": str(total_loans),
            "accounts": [
                {"code": l.account_code, "name": l.account_name, "balance": str(l.debit - l.credit)}
                for l in flagged
            ],
        },
        "recommended_action": rule.recommended_action,
        "legislation_ref": rule.legislation_ref,
    }


def _eval_gst_check(rule, fy, tb, ref, ctx, config):
    """Check GST-related compliance."""
    from review.models import PendingTransaction

    check_type = config.get("check_type", "gst_ratio")

    if check_type == "gst_ratio":
        # Compare GST claimed vs revenue
        revenue = abs(tb["totals"].get("revenue", ZERO))
        if revenue == ZERO:
            return None

        # Sum GST amounts from confirmed transactions
        gst_total = PendingTransaction.objects.filter(
            job__entity=fy.entity,
            is_confirmed=True,
        ).aggregate(total_gst=Sum("gst_amount"))["total_gst"] or ZERO

        if revenue > ZERO:
            gst_ratio = (gst_total / revenue * Decimal("100")).quantize(Decimal("0.1"))
            benchmark = ref.get("gst_benchmark_ratio", Decimal("11"))

            if gst_ratio > benchmark + Decimal("5"):
                return {
                    "rule_id": rule.rule_id,
                    "tier": 2,
                    "severity": rule.severity,
                    "title": rule.title,
                    "description": rule.description.format(
                        ratio=f"{gst_ratio}%",
                        benchmark=f"{benchmark}%",
                        gst_total=f"${gst_total:,.2f}",
                        revenue=f"${revenue:,.2f}",
                        entity_name=ctx.get("entity_name", ""),
                    ),
                    "affected_accounts": [],
                    "calculated_values": {
                        "gst_ratio": str(gst_ratio),
                        "benchmark": str(benchmark),
                        "gst_total": str(gst_total),
                        "revenue": str(revenue),
                    },
                    "recommended_action": rule.recommended_action,
                    "legislation_ref": rule.legislation_ref,
                }

    elif check_type == "gst_unclassified":
        # Check for unclassified transactions
        unclassified = PendingTransaction.objects.filter(
            job__entity=fy.entity,
            is_confirmed=False,
        ).count()

        if unclassified > 0:
            return {
                "rule_id": rule.rule_id,
                "tier": 2,
                "severity": rule.severity,
                "title": rule.title,
                "description": rule.description.format(
                    count=unclassified,
                    entity_name=ctx.get("entity_name", ""),
                ),
                "affected_accounts": [],
                "calculated_values": {"unclassified_count": unclassified},
                "recommended_action": rule.recommended_action,
                "legislation_ref": rule.legislation_ref,
            }

    return None


def _eval_superannuation(rule, fy, tb, ref, ctx, config):
    """Check superannuation compliance (SG rate, timing)."""
    wages_keywords = config.get("wages_keywords", ["wages", "salary", "salaries"])
    super_keywords = config.get("super_keywords", ["superannuation", "super guarantee", "super expense"])

    wages_total = ZERO
    super_total = ZERO
    wages_accounts = []
    super_accounts = []

    for line in tb["lines"]:
        name_lower = line.account_name.lower()
        net = abs(line.effective_dr - line.effective_cr)

        if any(kw in name_lower for kw in wages_keywords):
            wages_total += net
            wages_accounts.append(line.account_code)
        if any(kw in name_lower for kw in super_keywords):
            super_total += net
            super_accounts.append(line.account_code)

    if wages_total == ZERO:
        return None

    # Use the ATO year-aware rate table; allow ref_data override
    from core.risk_modules.cluster_sgc import _get_sg_rate_for_fy
    sg_rate = _get_sg_rate_for_fy(fy) * Decimal("100")  # as percentage for this legacy path
    sg_rate_override = ref.get("sg_rate")
    if sg_rate_override:
        try:
            sg_rate = Decimal(str(sg_rate_override))
        except Exception:
            pass
    expected_super = (wages_total * sg_rate / Decimal("100")).quantize(Decimal("0.01"))
    shortfall = expected_super - super_total

    # Allow 5% tolerance
    tolerance = expected_super * Decimal("0.05")

    if shortfall > tolerance:
        return {
            "rule_id": rule.rule_id,
            "tier": 2,
            "severity": rule.severity,
            "title": rule.title,
            "description": rule.description.format(
                wages=f"${wages_total:,.2f}",
                super_total=f"${super_total:,.2f}",
                expected=f"${expected_super:,.2f}",
                shortfall=f"${shortfall:,.2f}",
                sg_rate=f"{sg_rate}%",
                entity_name=ctx.get("entity_name", ""),
            ),
            "affected_accounts": wages_accounts + super_accounts,
            "calculated_values": {
                "wages_total": str(wages_total),
                "super_total": str(super_total),
                "expected_super": str(expected_super),
                "shortfall": str(shortfall),
                "sg_rate": str(sg_rate),
            },
            "recommended_action": rule.recommended_action,
            "legislation_ref": rule.legislation_ref,
        }
    return None


def _eval_trust_distribution(rule, fy, tb, ref, ctx, config):
    """Check trust-specific compliance."""
    if ctx.get("entity_type") != "trust":
        return None

    check_type = config.get("check_type", "undistributed")

    if check_type == "undistributed":
        # Check if trust has net income but no distribution recorded
        net_income = tb["totals"].get("net_profit", ZERO)
        if net_income > ZERO:
            # Check if distributions exist
            from core.models import TrustDistribution
            distributions = TrustDistribution.objects.filter(financial_year=fy)
            if not distributions.exists():
                return {
                    "rule_id": rule.rule_id,
                    "tier": 2,
                    "severity": rule.severity,
                    "title": rule.title,
                    "description": rule.description.format(
                        net_income=f"${net_income:,.2f}",
                        entity_name=ctx.get("entity_name", ""),
                    ),
                    "affected_accounts": [],
                    "calculated_values": {"net_income": str(net_income)},
                    "recommended_action": rule.recommended_action,
                    "legislation_ref": rule.legislation_ref,
                }
    return None


def _eval_expense_benchmark(rule, fy, tb, ref, ctx, config):
    """Check expense categories against ATO industry benchmarks."""
    expense_keywords = config.get("expense_keywords", [])
    benchmark_key = config.get("benchmark_key", "")
    revenue = abs(tb["totals"].get("revenue", ZERO))

    if revenue == ZERO:
        return None

    expense_total = ZERO
    expense_accounts = []
    for line in tb["lines"]:
        name_lower = line.account_name.lower()
        if any(kw.lower() in name_lower for kw in expense_keywords):
            expense_total += abs(line.effective_dr - line.effective_cr)
            expense_accounts.append(line.account_code)

    if expense_total == ZERO:
        return None

    ratio = (expense_total / revenue * Decimal("100")).quantize(Decimal("0.1"))
    benchmark = ref.get(benchmark_key, Decimal(str(config.get("threshold_value", 100))))

    if ratio > benchmark:
        return {
            "rule_id": rule.rule_id,
            "tier": 2,
            "severity": rule.severity,
            "title": rule.title,
            "description": rule.description.format(
                ratio=f"{ratio}%",
                benchmark=f"{benchmark}%",
                expense_total=f"${expense_total:,.2f}",
                revenue=f"${revenue:,.2f}",
                entity_name=ctx.get("entity_name", ""),
            ),
            "affected_accounts": expense_accounts[:5],
            "calculated_values": {
                "ratio": str(ratio),
                "benchmark": str(benchmark),
                "expense_total": str(expense_total),
                "revenue": str(revenue),
            },
            "recommended_action": rule.recommended_action,
            "legislation_ref": rule.legislation_ref,
        }
    return None


def _eval_tax_provision(rule, fy, tb, ref, ctx, config):
    """Check if a profitable company is missing an income tax provision.

    Fires when:
      - entity_type == 'company'
      - net profit > 0  (revenue exceeds expenses)
      - income tax expense balance < 1% of net profit
      - no AdjustingJournal with journal_type='tax_provision' is posted
    """
    from core.models import AdjustingJournal

    if ctx.get("entity_type") != "company":
        return None

    # In the risk engine, revenue is negative (credit) and expenses positive
    # (debit), so net_profit < 0 means the company is profitable.
    raw_net_profit = tb["totals"].get("net_profit", ZERO)
    if raw_net_profit >= ZERO:
        # Not profitable — no provision needed
        return None

    profit = abs(raw_net_profit)

    # Check for existing income tax expense on the TB
    tax_keywords = ["income tax", "tax expense", "tax provision"]
    tax_balance = ZERO
    for line in tb["lines"]:
        name_lower = line.account_name.lower()
        if any(kw in name_lower for kw in tax_keywords):
            tax_balance += abs(line.effective_dr - line.effective_cr)

    threshold = profit * Decimal("0.01")
    if tax_balance >= threshold:
        return None

    # Check for a posted tax_provision journal
    has_journal = AdjustingJournal.objects.filter(
        financial_year=fy,
        journal_type="tax_provision",
        status="posted",
    ).exists()
    if has_journal:
        return None

    return {
        "rule_id": rule.rule_id,
        "tier": 2,
        "severity": rule.severity,
        "title": rule.title,
        "description": rule.description.format(
            entity_name=ctx.get("entity_name", ""),
            net_profit=f"${profit:,.2f}",
        ),
        "recommended_action": rule.recommended_action,
        "legislation_ref": rule.legislation_ref,
    }


def _eval_prior_year_all_zeros(rule, fy, tb, ref, ctx, config):
    """Check if ALL prior year comparatives are zero or null."""
    if not tb["lines"]:
        return None

    has_any_prior = False
    for line in tb["lines"]:
        prior_dr = getattr(line, "prior_debit", None) or ZERO
        prior_cr = getattr(line, "prior_credit", None) or ZERO
        if prior_dr != ZERO or prior_cr != ZERO:
            has_any_prior = True
            break

    if has_any_prior:
        return None  # At least one line has prior data — rule does not trigger

    # All prior year values are zero — trigger
    import hashlib, json as _json
    finding_key = hashlib.sha256(_json.dumps({
        "entity_id": str(fy.entity_id),
        "financial_year_id": str(fy.pk),
        "rule": "prior_year_all_zeros",
    }, sort_keys=True).encode()).hexdigest()

    return {
        "rule_id": rule.rule_id,
        "tier": 2,
        "severity": rule.severity,
        "title": rule.title,
        "description": rule.description.format(
            entity_name=ctx.get("entity_name", ""),
            year_label=ctx.get("year_label", ""),
        ),
        "affected_accounts": [],
        "calculated_values": {
            "finding_key": finding_key,
            "total_lines": len(tb["lines"]),
        },
        "recommended_action": rule.recommended_action,
        "legislation_ref": rule.legislation_ref,
    }


# Evaluator registry
TIER2_EVALUATORS = {
    "account_threshold": _eval_account_threshold,
    "ratio_check": _eval_ratio_check,
    "balance_sign": _eval_balance_sign,
    "solvency": _eval_solvency,
    "loan_check": _eval_loan_check,
    "gst_check": _eval_gst_check,
    "superannuation": _eval_superannuation,
    "trust_distribution": _eval_trust_distribution,
    "expense_benchmark": _eval_expense_benchmark,
    "tax_provision": _eval_tax_provision,
    "prior_year_all_zeros": _eval_prior_year_all_zeros,
}


# ============================================================================
# FLAG CREATION
# ============================================================================

def _compute_flag_hash(financial_year_id, rule_id, description):
    """Compute deduplication hash for a risk flag."""
    import hashlib
    key = f"{financial_year_id}:{rule_id}:{description}"
    return hashlib.md5(key.encode()).hexdigest()


def _create_flag(financial_year, run_id, flag_data):
    """Create a RiskFlag record, or update if same rule already exists open."""
    from core.models import RiskFlag

    # Check if an open flag with this rule_id already exists for this FY
    existing = RiskFlag.objects.filter(
        financial_year=financial_year,
        rule_id=flag_data["rule_id"],
        status__in=["open", "reviewed"],
    ).first()

    if existing:
        # Update the existing flag with new data
        existing.run_id = run_id
        existing.description = flag_data["description"]
        existing.calculated_values = flag_data["calculated_values"]
        existing.severity = flag_data["severity"]
        existing.save()
        return existing

    # Create new flag
    return RiskFlag.objects.create(
        financial_year=financial_year,
        run_id=run_id,
        rule_id=flag_data["rule_id"],
        tier=flag_data["tier"],
        severity=flag_data["severity"],
        title=flag_data["title"],
        description=flag_data["description"],
        affected_accounts=flag_data.get("affected_accounts", []),
        calculated_values=flag_data.get("calculated_values", {}),
        recommended_action=flag_data.get("recommended_action", ""),
        legislation_ref=flag_data.get("legislation_ref", ""),
    )
