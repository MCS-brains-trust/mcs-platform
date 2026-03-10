"""
Eva Clarification System
========================
Provides interactive clarification dialogues on Eva findings.

When Eva raises a finding, she may ask the accountant targeted questions
to refine her assessment. The accountant's answers are stored as
EvaClarification records and used to:
  1. Re-evaluate the finding severity/status
  2. Learn for future reviews (same account in future years)

Question definitions are stored here per check_name.
"""
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Clarification question definitions
# ---------------------------------------------------------------------------
# Each entry maps a check_name to a list of question dicts.
# Questions are asked in order; follow-up questions may be triggered by
# the outcome of a previous answer.
#
# Each question dict:
#   id          — unique key within the check (used to look up answers)
#   text        — question shown to the accountant
#   options     — list of quick-answer buttons (label, value, outcome_hint)
#   follow_up   — dict mapping option value -> next question id (optional)
#   context_var — name of a calculated_values key to interpolate into text
# ---------------------------------------------------------------------------

CLARIFICATION_QUESTIONS = {
    "div7a": [
        {
            "id": "borrower_type",
            "text": "Who is '{borrower_name}'? What type of entity is the borrower?",
            "context_var": "borrower_name",  # pulled from finding.calculated_values
            "options": [
                {
                    "label": "Related Company (Pty Ltd)",
                    "value": "related_company",
                    "outcome_hint": "dismiss",
                    "help": "Div 7A does not apply to loans between companies",
                },
                {
                    "label": "Individual (Director / Shareholder)",
                    "value": "individual_director",
                    "outcome_hint": "confirm",
                    "help": "Div 7A applies — loan agreement required",
                },
                {
                    "label": "Spouse / Associate of Director",
                    "value": "individual_associate",
                    "outcome_hint": "confirm",
                    "help": "Div 7A applies — associate is a deemed shareholder",
                },
                {
                    "label": "Related Trust",
                    "value": "related_trust",
                    "outcome_hint": "confirm",
                    "help": "Div 7A may apply — UPE / sub-trust rules",
                },
                {
                    "label": "Unrelated Third Party",
                    "value": "unrelated_third_party",
                    "outcome_hint": "dismiss",
                    "help": "Div 7A does not apply to arm's-length third parties",
                },
            ],
        },
        {
            "id": "loan_agreement_exists",
            "text": "Does a complying Division 7A loan agreement exist for this loan?",
            "trigger_on": {"borrower_type": ["individual_director", "individual_associate", "related_trust"]},
            "options": [
                {
                    "label": "Yes — agreement in place",
                    "value": "agreement_exists",
                    "outcome_hint": "reduce_severity",
                    "help": "Downgrade to Advisory — verify terms are complying",
                },
                {
                    "label": "No — no agreement",
                    "value": "no_agreement",
                    "outcome_hint": "confirm",
                    "help": "Critical — unfranked deemed dividend risk",
                },
                {
                    "label": "In progress",
                    "value": "agreement_in_progress",
                    "outcome_hint": "confirm",
                    "help": "Must be executed before lodgement date",
                },
            ],
        },
    ],
    "superannuation": [
        {
            "id": "payroll_system",
            "text": "Are superannuation payments made through a separate payroll system (e.g. Xero Payroll, MYOB Payroll) that may not be fully reflected in this trial balance?",
            "options": [
                {
                    "label": "Yes — payroll system used",
                    "value": "payroll_system_yes",
                    "outcome_hint": "reduce_severity",
                    "help": "Shortfall may be a timing/import issue — verify payroll records",
                },
                {
                    "label": "No — all super in this TB",
                    "value": "payroll_system_no",
                    "outcome_hint": "confirm",
                    "help": "Shortfall is real — lodge SG charge if not remediated",
                },
            ],
        },
    ],
    "tpar": [
        {
            "id": "tpar_industry",
            "text": "Is this entity actually in a TPAR-reportable industry (building, cleaning, courier, IT, security, or investigation services)?",
            "options": [
                {
                    "label": "Yes — TPAR reportable",
                    "value": "tpar_yes",
                    "outcome_hint": "confirm",
                    "help": "TPAR must be lodged by 28 August",
                },
                {
                    "label": "No — not a reportable industry",
                    "value": "tpar_no",
                    "outcome_hint": "dismiss",
                    "help": "Contractor payments in non-reportable industries are exempt",
                },
                {
                    "label": "Mixed — some reportable services",
                    "value": "tpar_mixed",
                    "outcome_hint": "confirm",
                    "help": "Lodge TPAR for the reportable portion only",
                },
            ],
        },
    ],
}


# ---------------------------------------------------------------------------
# Re-evaluation logic
# ---------------------------------------------------------------------------

def get_clarification_questions(finding):
    """
    Return the list of clarification questions applicable to this finding,
    with context variables interpolated from the finding's calculated_values.

    Returns a list of question dicts (copies, safe to mutate).
    Only returns questions that haven't been answered yet AND whose
    trigger conditions are met.
    """
    from core.models import EvaClarification

    check_name = finding.check_name
    all_questions = CLARIFICATION_QUESTIONS.get(check_name, [])
    if not all_questions:
        return []

    # Load existing answers for this finding
    answered = {
        c.question_id: c.answer_value
        for c in EvaClarification.objects.filter(finding=finding)
    }

    # Get calculated_values for context interpolation
    calculated = {}
    if hasattr(finding, "calculated_values") and finding.calculated_values:
        calculated = finding.calculated_values
    # Also try to pull from the Div7A assessment
    if check_name == "div7a" and not calculated.get("borrower_name"):
        borrower_name = _extract_div7a_borrower_name(finding)
        if borrower_name:
            calculated["borrower_name"] = borrower_name

    result = []
    for q in all_questions:
        qid = q["id"]

        # Skip already answered
        if qid in answered:
            continue

        # Check trigger condition
        trigger = q.get("trigger_on", {})
        if trigger:
            triggered = True
            for trigger_qid, trigger_values in trigger.items():
                if answered.get(trigger_qid) not in trigger_values:
                    triggered = False
                    break
            if not triggered:
                continue

        # Interpolate context variables into question text
        q_copy = dict(q)
        text = q_copy["text"]
        for key, val in calculated.items():
            text = text.replace("{" + key + "}", str(val))
        # Remove unreplaced placeholders gracefully
        import re
        text = re.sub(r"\{[^}]+\}", "[unknown]", text)
        q_copy["text"] = text
        result.append(q_copy)

    return result


def _extract_div7a_borrower_name(finding):
    """Extract the borrower name from the Div7A finding's plain_english_explanation."""
    import re
    text = finding.plain_english_explanation or ""
    # Pattern: "Loan (Loan - CST, 3571)" or "Director/Shareholder Loan (Loan - CST, 3571)"
    m = re.search(r"Loan\s*\(([^,)]+)", text)
    if m:
        return m.group(1).strip()
    # Fallback: look for account_name in the text
    m = re.search(r"account[:\s]+([A-Za-z0-9\s\-&]+)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def reevaluate_finding(finding, clarification):
    """
    Re-evaluate a finding based on a new clarification answer.
    Returns a dict with:
        outcome      — "dismissed" | "confirmed" | "reduced" | "pending"
        message      — human-readable explanation of the outcome
        new_severity — new severity if changed, else None
        new_status   — new status if changed, else None
    """
    check_name = finding.check_name
    qid = clarification.question_id
    answer = clarification.answer_value
    detail = clarification.answer_detail or ""

    outcome = clarification.outcome_hint or "pending"
    message = ""
    new_severity = None
    new_status = None

    if check_name == "div7a":
        if qid == "borrower_type":
            if answer == "related_company":
                outcome = "dismissed"
                message = (
                    "Division 7A does not apply to loans between companies. "
                    "This finding has been dismissed. "
                    "Eva has noted that '{borrower}' is a related company for future reviews."
                )
                new_status = "addressed"
            elif answer == "unrelated_third_party":
                outcome = "dismissed"
                message = (
                    "Division 7A does not apply to arm's-length third-party loans. "
                    "This finding has been dismissed."
                )
                new_status = "addressed"
            elif answer in ("individual_director", "individual_associate"):
                outcome = "confirmed"
                message = (
                    "Division 7A applies. A complying loan agreement must be in place "
                    "before the lodgement date to avoid an unfranked deemed dividend."
                )
            elif answer == "related_trust":
                outcome = "confirmed"
                message = (
                    "Division 7A may apply to UPEs from related trusts. "
                    "Review sub-trust arrangements and TD 2022/11."
                )

        elif qid == "loan_agreement_exists":
            if answer == "agreement_exists":
                outcome = "reduced"
                new_severity = "advisory"
                message = (
                    "Severity reduced to Advisory. Verify the loan agreement terms comply "
                    "with s 109N (interest rate ≥ benchmark, minimum yearly repayments met)."
                )
            elif answer == "no_agreement":
                outcome = "confirmed"
                message = (
                    "No complying loan agreement exists. The full loan balance is at risk "
                    "of being treated as an unfranked deemed dividend. "
                    "Generate a complying agreement immediately."
                )

    elif check_name == "superannuation":
        if qid == "payroll_system":
            if answer == "payroll_system_yes":
                outcome = "reduced"
                new_severity = "advisory"
                message = (
                    "Severity reduced to Advisory. Verify super payments in the payroll "
                    "system match the required SG amount. Obtain payroll reconciliation."
                )
            else:
                outcome = "confirmed"
                message = "Superannuation shortfall confirmed. Lodge SG charge if not remediated before lodgement."

    elif check_name == "tpar":
        if qid == "tpar_industry":
            if answer == "tpar_no":
                outcome = "dismissed"
                message = "TPAR obligation dismissed — entity is not in a reportable industry."
                new_status = "addressed"
            elif answer in ("tpar_yes", "tpar_mixed"):
                outcome = "confirmed"
                message = "TPAR must be lodged by 28 August. Collate all contractor ABNs and payment amounts."

    # Apply borrower name to message if available
    borrower = _extract_div7a_borrower_name(finding) or "the borrower"
    message = message.replace("{borrower}", borrower)

    return {
        "outcome": outcome,
        "message": message,
        "new_severity": new_severity,
        "new_status": new_status,
    }


def build_learning_note(finding, clarification):
    """
    Build a learning note to attach to future findings for the same account.
    Returns a string or None.
    """
    check_name = finding.check_name
    answer = clarification.answer_value

    if check_name == "div7a" and clarification.question_id == "borrower_type":
        borrower = _extract_div7a_borrower_name(finding) or "this account"
        if answer == "related_company":
            return f"Previously clarified ({finding.eva_review.financial_year.year_label}): '{borrower}' is a related company — Div 7A does not apply."
        elif answer in ("individual_director", "individual_associate"):
            return f"Previously clarified ({finding.eva_review.financial_year.year_label}): '{borrower}' is an individual — Div 7A applies."
        elif answer == "related_trust":
            return f"Previously clarified ({finding.eva_review.financial_year.year_label}): '{borrower}' is a related trust — UPE rules apply."

    return None
