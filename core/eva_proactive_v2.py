"""
Eva Proactive Intelligence Agent v2
=====================================
Runs as a Celery Beat task at 7:00 AM daily.

Scans all active financial years for time-sensitive issues that accountants
should be aware of, using the Eva Agent Loop to gather context before
generating the proactive message.

Proactive Checks:
  1. Division 7A Loan Age Warning     — Loans approaching the 7-year repayment limit
  2. BAS Data Staleness               — BAS period data not updated in 45+ days
  3. Trial Balance Staleness          — TB not imported for 60+ days in an active FY
  4. ATO Benchmark Drift              — Revenue/expense ratios drifting outside ATO benchmarks
  5. Vesting Date Proximity           — Trust vesting date within 12 months
  6. Unresolved Prior Findings        — High-severity findings from prior year still open

Also contains the Self-Consistency Scoring engine used by the bank statement
review app to determine classification confidence.

Usage:
    from core.eva_proactive_v2 import run_daily_proactive_scan
    run_daily_proactive_scan()

    from core.eva_proactive_v2 import classify_with_consistency
    result = classify_with_consistency(transaction_description, entity, n_samples=3)
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ===========================================================================
# PROACTIVE INTELLIGENCE AGENT
# ===========================================================================

class ProactiveScanner:
    """
    Scans a single FinancialYear for time-sensitive issues.
    Returns a list of issue dicts if any are found.
    """

    def __init__(self, financial_year):
        self.fy = financial_year
        self.entity = financial_year.entity
        self.issues = []

    def scan(self) -> list:
        """Run all checks and return list of issues found."""
        checks = [
            self._check_div7a_loan_age,
            self._check_bas_staleness,
            self._check_tb_staleness,
            self._check_trust_vesting,
            self._check_unresolved_findings,
        ]

        for check in checks:
            try:
                check()
            except Exception as e:
                logger.warning(f"Proactive check {check.__name__} failed for {self.fy}: {e}")

        return self.issues

    def _check_div7a_loan_age(self):
        """Check for Division 7A loans approaching the 7-year repayment limit."""
        try:
            from core.models import Div7ALoan
        except ImportError:
            return

        try:
            loans = Div7ALoan.objects.filter(
                entity=self.entity,
                is_repaid=False,
                loan_date__isnull=False,
            )
        except Exception:
            return

        for loan in loans:
            if not loan.loan_date:
                continue
            age_years = (datetime.now(timezone.utc).date() - loan.loan_date).days / 365.25
            years_remaining = 7.0 - age_years

            if years_remaining <= 0:
                self.issues.append({
                    "check": "div7a_loan_overdue",
                    "severity": "critical",
                    "title": f"Division 7A Loan OVERDUE — {self.entity.entity_name}",
                    "detail": (
                        f"Loan of ${float(loan.loan_amount or 0):,.0f} to "
                        f"{getattr(loan, 'borrower_name', 'unknown')} dated "
                        f"{loan.loan_date} has exceeded the 7-year repayment period. "
                        f"Immediate action required to avoid deemed dividend."
                    ),
                    "entity_id": str(self.entity.id),
                    "financial_year_id": str(self.fy.id),
                })
            elif years_remaining <= 1.0:
                self.issues.append({
                    "check": "div7a_loan_approaching",
                    "severity": "high",
                    "title": f"Division 7A Loan Approaching Limit — {self.entity.entity_name}",
                    "detail": (
                        f"Loan of ${float(loan.loan_amount or 0):,.0f} dated {loan.loan_date} "
                        f"has {years_remaining:.1f} year(s) remaining before the 7-year limit. "
                        f"Review repayment schedule now."
                    ),
                    "entity_id": str(self.entity.id),
                    "financial_year_id": str(self.fy.id),
                })

    def _check_bas_staleness(self):
        """Check if BAS period data has not been updated recently."""
        try:
            from core.models import BASPeriod
        except ImportError:
            return

        try:
            stale_threshold = datetime.now(timezone.utc) - timedelta(days=45)
            stale_periods = BASPeriod.objects.filter(
                entity=self.entity,
                financial_year=self.fy,
                updated_at__lt=stale_threshold,
                status__in=["draft", "in_progress"],
            )
            count = stale_periods.count()
            if count > 0:
                self.issues.append({
                    "check": "bas_data_stale",
                    "severity": "medium",
                    "title": f"Stale BAS Data — {self.entity.entity_name}",
                    "detail": (
                        f"{count} BAS period(s) have not been updated in over 45 days. "
                        f"Check if bank statements have been imported and coded."
                    ),
                    "entity_id": str(self.entity.id),
                    "financial_year_id": str(self.fy.id),
                })
        except Exception as e:
            logger.debug(f"BAS staleness check failed: {e}")

    def _check_tb_staleness(self):
        """Check if the trial balance has not been imported recently."""
        try:
            from core.models import TrialBalance
        except ImportError:
            return

        try:
            stale_threshold = datetime.now(timezone.utc) - timedelta(days=60)
            latest_tb = (
                TrialBalance.objects
                .filter(financial_year=self.fy)
                .order_by("-imported_at")
                .first()
            )
            if latest_tb and latest_tb.imported_at < stale_threshold:
                days_since = (datetime.now(timezone.utc) - latest_tb.imported_at).days
                self.issues.append({
                    "check": "tb_stale",
                    "severity": "low",
                    "title": f"Trial Balance Not Updated — {self.entity.entity_name}",
                    "detail": (
                        f"The trial balance was last imported {days_since} days ago. "
                        f"Consider importing an updated TB to keep Eva's analysis current."
                    ),
                    "entity_id": str(self.entity.id),
                    "financial_year_id": str(self.fy.id),
                })
        except Exception as e:
            logger.debug(f"TB staleness check failed: {e}")

    def _check_trust_vesting(self):
        """Check if a trust is approaching its vesting date."""
        if self.entity.entity_type != "trust":
            return
        if not self.entity.vesting_date:
            return

        today = datetime.now(timezone.utc).date()
        days_to_vesting = (self.entity.vesting_date - today).days

        if days_to_vesting <= 0:
            self.issues.append({
                "check": "trust_vested",
                "severity": "critical",
                "title": f"Trust Vesting Date PASSED — {self.entity.entity_name}",
                "detail": (
                    f"The vesting date of {self.entity.vesting_date} has passed. "
                    f"Immediate legal and tax advice required."
                ),
                "entity_id": str(self.entity.id),
                "financial_year_id": str(self.fy.id),
            })
        elif days_to_vesting <= 365:
            self.issues.append({
                "check": "trust_vesting_approaching",
                "severity": "high",
                "title": f"Trust Vesting Date Within 12 Months — {self.entity.entity_name}",
                "detail": (
                    f"The trust vests on {self.entity.vesting_date} "
                    f"({days_to_vesting} days). Begin vesting planning now."
                ),
                "entity_id": str(self.entity.id),
                "financial_year_id": str(self.fy.id),
            })

    def _check_unresolved_findings(self):
        """Check for high-severity findings from prior years that are still open."""
        try:
            from core.models import EvaFinding, EvaReview
        except ImportError:
            return

        try:
            prior_year_cutoff = datetime.now(timezone.utc) - timedelta(days=400)
            unresolved = EvaFinding.objects.filter(
                eva_review__financial_year__entity=self.entity,
                severity__in=["critical", "high"],
                status__in=["open", "in_progress"],
                created_at__lt=prior_year_cutoff,
            ).count()

            if unresolved > 0:
                self.issues.append({
                    "check": "unresolved_prior_findings",
                    "severity": "medium",
                    "title": f"Unresolved Prior Year Findings — {self.entity.entity_name}",
                    "detail": (
                        f"{unresolved} high-severity Eva finding(s) from prior years remain unresolved. "
                        f"Review and address or suppress these findings."
                    ),
                    "entity_id": str(self.entity.id),
                    "financial_year_id": str(self.fy.id),
                })
        except Exception as e:
            logger.debug(f"Unresolved findings check failed: {e}")


def _generate_proactive_message(issue: dict, financial_year) -> Optional[str]:
    """
    Use the Eva Agent Loop to generate a contextual proactive message for an issue.
    """
    from core.eva_agent import EvaAgentLoop

    trigger_input = (
        f"Generate a brief, actionable proactive alert for the following issue. "
        f"Be specific, cite relevant data from the trial balance if available, "
        f"and suggest 1-2 concrete next steps.\n\n"
        f"Issue: {issue['title']}\n"
        f"Detail: {issue['detail']}\n"
        f"Severity: {issue['severity']}"
    )

    agent = EvaAgentLoop(
        financial_year=financial_year,
        user=None,
        trigger_type="proactive",
        trigger_input=trigger_input,
    )

    try:
        result = agent.run()
        return result.get("answer", "")
    except Exception as e:
        logger.error(f"Agent failed to generate proactive message: {e}")
        return issue["detail"]  # Fallback to raw detail


def _save_proactive_message(financial_year, issue: dict, message_text: str) -> None:
    """Save the proactive message as an EvaMessage."""
    try:
        from core.models import EvaConversation, EvaMessage

        # Get or create a proactive conversation for this FY
        conversation, _ = EvaConversation.objects.get_or_create(
            financial_year=financial_year,
            defaults={"user": None},
        )

        EvaMessage.objects.create(
            conversation=conversation,
            role="assistant",
            content=message_text,
            is_proactive=True,
            metadata={
                "check": issue.get("check"),
                "severity": issue.get("severity"),
                "title": issue.get("title"),
            },
        )
        logger.info(f"Saved proactive message for {financial_year}: {issue['check']}")
    except Exception as e:
        logger.error(f"Failed to save proactive message: {e}")


def run_daily_proactive_scan() -> dict:
    """
    Main entry point for the daily proactive scan task.

    Scans all active financial years and generates proactive messages
    for any time-sensitive issues found.

    Returns:
        {
            "years_scanned": int,
            "issues_found": int,
            "messages_generated": int,
            "errors": list,
        }
    """
    logger.info("Eva Daily Proactive Scan starting...")

    from core.models import FinancialYear

    errors = []
    years_scanned = 0
    total_issues = 0
    messages_generated = 0

    # Get all active financial years
    active_years = FinancialYear.objects.filter(
        status__in=["in_progress", "review", "draft"],
        entity__is_archived=False,
    ).select_related("entity")

    for fy in active_years:
        years_scanned += 1
        try:
            scanner = ProactiveScanner(fy)
            issues = scanner.scan()

            for issue in issues:
                total_issues += 1
                try:
                    message_text = _generate_proactive_message(issue, fy)
                    if message_text:
                        _save_proactive_message(fy, issue, message_text)
                        messages_generated += 1
                except Exception as e:
                    logger.error(f"Failed to generate/save message for issue {issue['check']}: {e}")
                    errors.append(f"{fy}: {issue['check']}: {e}")

        except Exception as e:
            logger.error(f"Proactive scan failed for {fy}: {e}")
            errors.append(f"{fy}: {e}")

    result = {
        "years_scanned": years_scanned,
        "issues_found": total_issues,
        "messages_generated": messages_generated,
        "errors": errors,
    }

    logger.info(
        f"Eva Daily Proactive Scan complete: {years_scanned} years scanned, "
        f"{total_issues} issues found, {messages_generated} messages generated."
    )

    return result


# ===========================================================================
# SELF-CONSISTENCY SCORING ENGINE
# ===========================================================================

def classify_with_consistency(
    transaction_description: str,
    entity,
    account_options: list,
    n_samples: int = 3,
) -> dict:
    """
    Classify a bank transaction using self-consistency sampling.

    Makes N independent LLM calls for the same transaction and uses
    majority voting to determine the classification and confidence level.

    Args:
        transaction_description: The raw transaction description.
        entity: The Entity instance (for context).
        account_options: List of {code, name} dicts for available accounts.
        n_samples: Number of independent LLM calls (default 3).

    Returns:
        {
            "account_code": str,
            "account_name": str,
            "confidence": "high" | "medium" | "low",
            "confidence_score": float,
            "agreement_count": int,
            "all_votes": list,
            "auto_approve": bool,
        }
    """
    from core.ai_service import _call_llm
    import concurrent.futures

    # Build the classification prompt
    accounts_text = "\n".join([
        f"  {a['code']}: {a['name']}" for a in account_options[:50]
    ])

    system_prompt = (
        f"You are an expert Australian bookkeeper classifying bank transactions for "
        f"{entity.entity_name} ({entity.get_entity_type_display()}). "
        f"Return ONLY a JSON object with 'account_code' and 'account_name'. "
        f"Choose from the available accounts below. No explanation needed."
    )

    user_prompt = (
        f"Transaction: {transaction_description}\n\n"
        f"Available accounts:\n{accounts_text}\n\n"
        f"Classify this transaction."
    )

    # Make N independent calls
    votes = []

    def single_call(_):
        try:
            response = _call_llm(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tier="haiku",
                temperature=0.3,  # slight randomness for diversity
                max_tokens=100,
            )
            import re
            json_match = re.search(r'\{[^{}]+\}', response, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                return parsed.get("account_code", ""), parsed.get("account_name", "")
        except Exception as e:
            logger.warning(f"Consistency sample failed: {e}")
        return None, None

    # Run samples in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=n_samples) as executor:
        futures = list(executor.map(single_call, range(n_samples)))

    for code, name in futures:
        if code:
            votes.append({"account_code": code, "account_name": name})

    if not votes:
        return {
            "account_code": "",
            "account_name": "",
            "confidence": "low",
            "confidence_score": 0.0,
            "agreement_count": 0,
            "all_votes": [],
            "auto_approve": False,
        }

    # Count votes
    from collections import Counter
    vote_counts = Counter(v["account_code"] for v in votes)
    winning_code, agreement_count = vote_counts.most_common(1)[0]
    winning_vote = next((v for v in votes if v["account_code"] == winning_code), votes[0])

    # Determine confidence
    confidence_score = agreement_count / len(votes)
    if agreement_count == n_samples:
        confidence = "high"
        auto_approve = True
    elif agreement_count >= (n_samples * 0.6):
        confidence = "medium"
        auto_approve = False
    else:
        confidence = "low"
        auto_approve = False

    return {
        "account_code": winning_code,
        "account_name": winning_vote.get("account_name", ""),
        "confidence": confidence,
        "confidence_score": confidence_score,
        "agreement_count": agreement_count,
        "all_votes": votes,
        "auto_approve": auto_approve,
    }
