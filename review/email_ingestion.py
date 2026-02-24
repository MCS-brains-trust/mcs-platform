"""
AI Transaction Classification Service for Bank Statement Processing.

Provides:
- Learning database pattern matching (per-client and global)
- AI-powered classification via Anthropic Claude API
- Dynamic chart of accounts from database
- Claude Vision PDF extraction (fallback for scanned PDFs)
"""
import json
import logging
import os
import re
from decimal import Decimal

from django.conf import settings

from .models import PendingTransaction, ReviewActivity, ReviewJob, TransactionPattern

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PDF Transaction Extraction (Anthropic Claude Vision)
# ---------------------------------------------------------------------------

def _get_anthropic_client():
    """Get an Anthropic client using the API key from settings."""
    import anthropic
    from django.conf import settings as django_settings

    api_key = getattr(django_settings, "ANTHROPIC_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is not configured")
    return anthropic.Anthropic(api_key=api_key)


def extract_transactions_from_pdf(pdf_b64, filename="statement.pdf"):
    """
    Use Anthropic Claude to extract transactions from a bank statement PDF.
    Returns dict with: opening_balance, closing_balance, account_name,
    bsb, account_number, period_start, period_end, transactions[]
    """
    client = _get_anthropic_client()

    system_prompt = (
        "You are a bank statement parser for an Australian accounting firm. "
        "Extract ALL transactions from the provided bank statement PDF. "
        "For each transaction, extract: date (DD/MM/YYYY format), "
        "description (the full transaction description as shown), "
        "amount (positive for credits/deposits, negative for debits/withdrawals). "
        "Return a JSON object with keys: opening_balance (number), "
        "closing_balance (number), account_name (string), bsb (string), "
        "account_number (string), period_start (string DD/MM/YYYY), "
        "period_end (string DD/MM/YYYY), "
        "transactions (array of {date, description, amount}). "
        "Return ONLY valid JSON — no markdown, no code fences, just the JSON object."
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=16384,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": "Extract ALL transactions from this bank statement PDF. Return valid JSON only.",
                        },
                    ],
                },
            ],
        )

        content = response.content[0].text
        # Strip any markdown code fences if present
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        data = json.loads(content)
        return data

    except Exception as e:
        logger.error(f"PDF extraction failed for {filename}: {e}")
        return None


# ---------------------------------------------------------------------------
# AI Transaction Classification
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Dynamic Chart of Accounts from Database
# ---------------------------------------------------------------------------

def _get_chart_of_accounts_prompt(entity_type=None):
    """
    Build the CHART OF ACCOUNTS prompt dynamically from the database.
    Uses entity-type-specific accounts when entity_type is provided,
    otherwise falls back to company accounts as a sensible default.
    """
    from core.models import ChartOfAccount

    et = entity_type or "company"
    accounts = ChartOfAccount.objects.filter(
        entity_type=et, is_active=True
    ).order_by("section", "display_order")

    if not accounts.exists():
        # Fallback: try any entity type
        accounts = ChartOfAccount.objects.filter(
            is_active=True
        ).order_by("section", "display_order")[:200]

    # Group by section
    sections = {}
    for acct in accounts:
        section_label = acct.get_section_display()
        if section_label not in sections:
            sections[section_label] = []
        tax_hint = f" ({acct.tax_code})" if acct.tax_code else ""
        sections[section_label].append(
            f"{acct.account_code} {acct.account_name}{tax_hint}"
        )

    # Build the prompt — only include P&L and key balance sheet accounts
    # to keep the prompt concise for the AI
    lines = ["CHART OF ACCOUNTS:"]
    priority_sections = [
        "Revenue", "Cost of Sales", "Expenses",
        "Assets", "Liabilities", "Equity",
        "Capital Accounts", "P&L Appropriation", "Suspense",
    ]
    for section in priority_sections:
        if section in sections:
            acct_list = ", ".join(sections[section][:80])  # Cap per section
            lines.append(f"{section.upper()}: {acct_list}")

    lines.append("")
    lines.append("CLASSIFICATION RULES:")
    lines.append("- Fuel (BP, Shell, Ampol, EG Group, 7-Eleven, United, Caltex) = Fuel & oil account, GST")
    lines.append("- Hardware (Bunnings, Total Tools) = Tools & equipment account, GST")
    lines.append("- Insurance = Insurance account, ITS")
    lines.append("- ATO payments = PAYG/Tax Payable account, ITS")
    lines.append("- Bank fees = Bank charges account, ITS")
    lines.append("- Telco (Telstra, Optus, Vodafone) = Telephone account, GST")
    lines.append("- Internal transfers = Loan/Drawing account, N-T")
    lines.append("- Customer payments/deposits = Sales account, GST")
    lines.append("- Interest earned = Interest received account, ITS")
    lines.append("- Rent/lease = Rent account, GST")
    lines.append("- Utilities (AGL, Origin, Energy Australia) = Electricity/gas account, GST")
    lines.append("- Wages/salary = Wages account, ITS")
    lines.append("- Super = Superannuation account, ITS")
    lines.append("- Software (Microsoft, Google, Xero, MYOB) = Computer/IT account, GST")
    lines.append("")
    lines.append("TAX TYPE CODES:")
    lines.append("- GST = GST on Income (for income) or GST on Expenses (for expenses)")
    lines.append("- ITS = Input Taxed (no GST component)")
    lines.append("- N-T = Not Reportable (balance sheet items, internal transfers)")
    lines.append("- GST-Free = GST Free Income or GST Free Expenses")
    lines.append("- ADS = Adjustment (non-standard)")
    lines.append("")
    lines.append("IMPORTANT: You MUST use account codes from the chart above. Do NOT invent codes.")
    lines.append("CONFIDENCE: 5=Certain, 4=Very likely, 3=Probable, 2=Uncertain, 1=Unknown")

    return "\n".join(lines)


# Keep a module-level fallback for backward compatibility
CHART_OF_ACCOUNTS = _get_chart_of_accounts_prompt.__doc__  # Will be replaced at runtime


def classify_transactions(transactions, entity=None, is_gst_registered=True):
    """
    Classify a list of transactions using:
    1. Learning database (per-client patterns) — highest priority
    2. AI API with entity-type-specific chart of accounts — for unknown transactions

    For GST-registered clients, calculates GST component.
    For non-GST clients, all tax types default to BAS Excluded.

    Returns list of dicts with classification results.
    """
    results = []
    unknown_txns = []
    unknown_indices = []

    # Determine entity type for chart of accounts lookup
    entity_type = entity.entity_type if entity else "company"

    # Step 1: Check learning database for known patterns
    for i, txn in enumerate(transactions):
        desc = txn.get("description", "")
        desc_normalised = _normalise_description(desc)

        pattern = _find_pattern(desc_normalised, entity)
        if pattern:
            tax_type = pattern.tax_type
            if not is_gst_registered and tax_type in ("GST on Income", "GST on Expenses"):
                tax_type = "BAS Excluded"

            results.append({
                "index": i,
                "account_code": pattern.account_code,
                "account_name": pattern.account_name,
                "tax_type": tax_type,
                "confidence": 5,
                "reasoning": f"Matched learning pattern (used {pattern.usage_count}x)",
                "from_learning": True,
            })
        else:
            results.append(None)  # Placeholder
            unknown_txns.append(txn)
            unknown_indices.append(i)

    # Step 2: AI classify unknown transactions in batches
    if unknown_txns:
        ai_results = _ai_classify_batch(
            unknown_txns, is_gst_registered, entity_type=entity_type
        )
        if len(ai_results) != len(unknown_indices):
            logger.warning(
                f"AI classification count mismatch: expected {len(unknown_indices)}, "
                f"got {len(ai_results)}. Falling back to Suspense for unmatched."
            )
        for idx, ai_result in zip(unknown_indices, ai_results):
            results[idx] = {
                "index": idx,
                "account_code": ai_result.get("accountCode", "0000"),
                "account_name": ai_result.get("accountName", "Suspense"),
                "tax_type": _map_tax_type(
                    ai_result.get("taxType", "GST"),
                    transactions[idx].get("amount", 0),
                    is_gst_registered,
                ),
                "confidence": ai_result.get("confidence", 1),
                "reasoning": ai_result.get("reasoning", ""),
                "from_learning": False,
            }
        # Fill any remaining unmatched indices with fallback
        matched_indices = set(unknown_indices[:len(ai_results)])
        for idx in unknown_indices:
            if idx not in matched_indices:
                results[idx] = {
                    "index": idx,
                    "account_code": "0000",
                    "account_name": "Suspense",
                    "tax_type": "BAS Excluded" if not is_gst_registered else "GST Free",
                    "confidence": 0,
                    "reasoning": "AI classification unavailable",
                    "from_learning": False,
                }

    return results


def _normalise_description(desc):
    """Normalise a transaction description for pattern matching."""
    # Remove dates, reference numbers, extra whitespace
    desc = re.sub(r"\d{2}/\d{2}/\d{2,4}", "", desc)
    desc = re.sub(r"\b\d{6,}\b", "", desc)  # Remove long numbers
    desc = re.sub(r"\s+", " ", desc).strip().upper()
    return desc


def _find_pattern(normalised_desc, entity=None):
    """
    Find a matching pattern in the learning database.
    Checks entity-specific patterns first, then global patterns.
    """
    if not normalised_desc:
        return None

    # Entity-specific pattern (exact match)
    if entity:
        pattern = TransactionPattern.objects.filter(
            entity=entity,
            description_pattern=normalised_desc,
        ).first()
        if pattern:
            return pattern

    # Entity-specific pattern (contains match)
    if entity:
        patterns = TransactionPattern.objects.filter(entity=entity)
        for p in patterns:
            if p.description_pattern in normalised_desc or normalised_desc in p.description_pattern:
                return p

    # Global pattern (exact match)
    pattern = TransactionPattern.objects.filter(
        entity__isnull=True,
        description_pattern=normalised_desc,
    ).first()
    if pattern:
        return pattern

    return None


def _map_tax_type(ai_tax_type, amount, is_gst_registered):
    """Map the AI's short tax type code to the full tax type label."""
    if not is_gst_registered:
        return "BAS Excluded"

    ai_tax_upper = (ai_tax_type or "").upper().strip()
    is_income = float(amount) >= 0

    mapping = {
        "GST": "GST on Income" if is_income else "GST on Expenses",
        "GST ON INCOME": "GST on Income",
        "GST ON EXPENSES": "GST on Expenses",
        "ITS": "Input Taxed",
        "INPUT TAXED": "Input Taxed",
        "N-T": "N-T",
        "NOT REPORTABLE": "N-T",
        "GST-FREE": "GST Free Income" if is_income else "GST Free Expenses",
        "GST FREE": "GST Free Income" if is_income else "GST Free Expenses",
        "GST FREE INCOME": "GST Free Income",
        "GST FREE EXPENSES": "GST Free Expenses",
        "BAS EXCLUDED": "BAS Excluded",
    }
    return mapping.get(ai_tax_upper, "GST on Expenses" if not is_income else "GST on Income")


def _ai_classify_batch(transactions, is_gst_registered=True, batch_size=15, entity_type=None):
    """
    Classify transactions using Anthropic Claude API with entity-type-specific chart of accounts.
    Processes in batches of batch_size.
    """
    client = _get_anthropic_client()
    all_results = []

    # Build the chart of accounts prompt from the database
    chart_prompt = _get_chart_of_accounts_prompt(entity_type)

    for i in range(0, len(transactions), batch_size):
        batch = transactions[i:i + batch_size]

        txn_list = "\n".join(
            f"{j + 1}. Date: {t.get('date', '')}, "
            f"Desc: \"{t.get('description', '')}\", "
            f"Amount: ${abs(float(t.get('amount', 0))):.2f} "
            f"({'CREDIT' if float(t.get('amount', 0)) >= 0 else 'DEBIT'})"
            for j, t in enumerate(batch)
        )

        gst_context = ""
        if is_gst_registered:
            gst_context = (
                "\n\nGST CONTEXT: This client IS registered for GST. "
                "Assign appropriate tax types (GST, ITS, N-T, GST-Free) "
                "based on the nature of each transaction."
            )
        else:
            gst_context = (
                "\n\nGST CONTEXT: This client is NOT registered for GST. "
                "All tax types should be 'BAS Excluded'."
            )

        entity_label = (entity_type or "company").replace("_", " ").title()
        prompt = (
            f"Classify these Australian bank transactions for a {entity_label} entity:\n\n"
            f"{chart_prompt}{gst_context}\n\n"
            f"TRANSACTIONS:\n{txn_list}\n\n"
            f"Return ONLY valid JSON (no markdown, no code fences): "
            f"{{\"classifications\": ["
            f"{{\"accountCode\": str, \"accountName\": str, "
            f"\"taxType\": str, \"confidence\": int 1-5, "
            f"\"reasoning\": str}}]}}"
        )

        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8192,
                system=(
                    "You are an Australian accounting AI assistant for MC&S Pty Ltd. "
                    "You classify bank transactions using the provided chart of accounts. "
                    "You MUST only use account codes from the chart provided. "
                    "If unsure, use account code 0000 (Suspense) with low confidence. "
                    "Return ONLY valid JSON — no markdown, no code fences, just the JSON object."
                ),
                messages=[{"role": "user", "content": prompt}],
            )

            content = response.content[0].text
            # Strip any markdown code fences if present
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            parsed = json.loads(content)
            classifications = (
                parsed.get("classifications")
                or parsed.get("results")
                or parsed.get("transactions")
                or []
            )

            # Pad with defaults if AI returned fewer results
            while len(classifications) < len(batch):
                classifications.append({
                    "accountCode": "0000",
                    "accountName": "Suspense",
                    "taxType": "N-T",
                    "confidence": 1,
                    "reasoning": "No classification returned",
                })

            all_results.extend(classifications[:len(batch)])

        except Exception as e:
            logger.error(f"AI classification failed: {e}")
            # Return defaults for the batch
            for _ in batch:
                all_results.append({
                    "accountCode": "0000",
                    "accountName": "Suspense",
                    "taxType": "N-T",
                    "confidence": 1,
                    "reasoning": f"Classification error: {str(e)}",
                })

    return all_results
