"""
Financial Statement Template Service — docxtpl pipeline.

Replaces the old python-docx programmatic approach (core/docgen.py) with
Jinja2-templated .docx files rendered via docxtpl.

Functions:
  format_amount           — Decimal → financial string
  aggregate_tb_lines      — Group/sum TB lines by normalised account name
  build_company_context   — Full Jinja2 context for company entity
  build_trust_context     — Full Jinja2 context for trust entity
  build_sole_trader_context — Full Jinja2 context for sole trader entity
  render_template         — Load + render a .docx template via DocxTemplate
  generate_financial_statements — Orchestrate all templates for a FY
  assemble_pdf_package    — Generate clean PDFs, merge into single package
"""
import io
import logging
import os
import tempfile
from collections import OrderedDict
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings

from core.libreoffice_utils import convert_docx_to_pdf

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. format_amount
# ---------------------------------------------------------------------------
def format_amount(value, show_negative_brackets=True):
    """Format a Decimal to financial string.

    - Zero / None → "-"
    - Negative with brackets → "(1,234)"
    - No $ sign in cells
    """
    if value is None:
        return "-"
    d = Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if d == 0:
        return "-"
    if d < 0 and show_negative_brackets:
        return f"({abs(d):,.0f})"
    return f"{d:,.0f}"


# ---------------------------------------------------------------------------
# 2. aggregate_tb_lines
# ---------------------------------------------------------------------------
def aggregate_tb_lines(queryset):
    """Group TrialBalanceLine queryset by normalised account_name, sum amounts.

    Grouping key: account_name.strip().lower()
    Display name: most frequent original casing within group.

    Returns list of dicts:
        [{"account_name": str, "cy_amount": Decimal, "py_amount": Decimal}, ...]
    """
    raw_count = 0
    agg = OrderedDict()      # norm_key → {cy, py, names: {name: count}}
    for line in queryset:
        raw_count += 1
        norm = line.account_name.strip().lower()
        cy = line.closing_balance
        py = line.prior_debit - line.prior_credit
        if norm in agg:
            agg[norm]["cy"] += cy
            agg[norm]["py"] += py
            agg[norm]["names"][line.account_name] = (
                agg[norm]["names"].get(line.account_name, 0) + 1
            )
        else:
            agg[norm] = {
                "cy": cy,
                "py": py,
                "names": {line.account_name: 1},
            }

    result = []
    for norm, data in agg.items():
        display_name = max(data["names"], key=data["names"].get)
        result.append({
            "account_name": display_name,
            "cy_amount": data["cy"],
            "py_amount": data["py"],
        })

    logger.info(
        "aggregate_tb_lines: %d raw lines -> %d aggregated rows",
        raw_count, len(result),
    )
    return result


# ---------------------------------------------------------------------------
# helpers — TB section extraction (mirrors docgen._get_tb_sections logic)
# ---------------------------------------------------------------------------
def _get_tb_sections(fy):
    """Extract trial balance lines grouped into financial statement sections."""
    lines = fy.trial_balance_lines.order_by("account_code").all()
    sections = {
        "trading_income": [],
        "cogs": [],
        "income": [],
        "expenses": [],
        "current_assets": [],
        "noncurrent_assets": [],
        "current_liabilities": [],
        "noncurrent_liabilities": [],
        "equity": [],
    }

    for line in lines:
        try:
            code_num = int(line.account_code.split(".")[0])
        except (ValueError, TypeError):
            continue

        cy = line.closing_balance
        py = line.prior_debit - line.prior_credit
        entry = {
            "account_code": line.account_code,
            "account_name": line.account_name,
            "cy_amount": cy,
            "py_amount": py,
        }

        name_lower = line.account_name.lower()
        is_cogs = any(kw in name_lower for kw in [
            "cost of", "opening stock", "closing stock", "purchases", "stock on hand",
        ])

        if code_num < 1000:
            is_other_income = any(kw in name_lower for kw in [
                "interest", "other", "fbt", "contribution", "dividend", "sundry",
            ])
            is_trading = any(kw in name_lower for kw in [
                "sales", "income", "takings", "revenue", "accommodation",
                "conference", "meals", "bar", "trading",
            ])
            if is_other_income:
                sections["income"].append(entry)
            elif is_trading:
                sections["trading_income"].append(entry)
            else:
                sections["income"].append(entry)
        elif code_num < 1200:
            sections["cogs"].append(entry)
        elif code_num < 2000:
            if is_cogs:
                sections["cogs"].append(entry)
            else:
                sections["expenses"].append(entry)
        elif code_num < 2500:
            sections["current_assets"].append(entry)
        elif code_num < 3000:
            sections["noncurrent_assets"].append(entry)
        elif code_num < 3500:
            sections["current_liabilities"].append(entry)
        elif code_num < 4000:
            sections["noncurrent_liabilities"].append(entry)
        elif code_num < 5000:
            sections["equity"].append(entry)
        elif code_num < 6000:
            sections["cogs"].append(entry)

    # Aggregate lines with the same account within each section.
    # Primary merge key: account_code (stable across renames in Xero/QBO).
    # Fallback for blank codes: case-insensitive, whitespace-normalised name.
    # Display name preference: names from lines with non-zero CY data are
    # weighted higher so that renamed accounts show the current-year name.
    for key in sections:
        raw = sections[key]
        if not raw:
            continue
        agg = OrderedDict()
        name_counts = {}
        for entry in raw:
            code = entry.get("account_code", "").strip()
            merge_key = code if code else entry["account_name"].strip().lower()
            weight = 10 if entry["cy_amount"] != 0 else 1
            if merge_key in agg:
                agg[merge_key]["cy_amount"] += entry["cy_amount"]
                agg[merge_key]["py_amount"] += entry["py_amount"]
                name_counts[merge_key][entry["account_name"]] = (
                    name_counts[merge_key].get(entry["account_name"], 0) + weight
                )
            else:
                agg[merge_key] = {
                    "account_name": entry["account_name"],
                    "cy_amount": entry["cy_amount"],
                    "py_amount": entry["py_amount"],
                }
                name_counts[merge_key] = {entry["account_name"]: weight}
        for mk in agg:
            best = max(name_counts[mk], key=name_counts[mk].get)
            agg[mk]["account_name"] = best
        sections[key] = list(agg.values())
        logger.debug(
            "_get_tb_sections [%s]: %d raw -> %d aggregated",
            key, len(raw), len(agg),
        )

    return sections


def _sum_section(items, field="cy_amount"):
    """Sum a list of dicts by field."""
    return sum(item[field] for item in items)


def _format_lines(items, credit_normal=False):
    """Add formatted amount strings to each item dict.

    For credit-normal accounts (liabilities, equity, income/revenue),
    the raw TB amount is negative (debit - credit).  Setting
    ``credit_normal=True`` negates the value before formatting so these
    accounts display as positive numbers in the financial statements.
    """
    for item in items:
        cy = item["cy_amount"]
        py = item["py_amount"]
        if credit_normal:
            cy = -cy if cy else cy
            py = -py if py else py
        item["cy_formatted"] = format_amount(cy)
        item["py_formatted"] = format_amount(py)
    return items


def _has_prior_year(fy):
    """Check if there is prior year data."""
    if not fy.prior_year:
        return False
    return fy.prior_year.trial_balance_lines.exists()


def _format_acn_abn(acn, abn):
    """Build a combined 'ACN: xxx / ABN: xxx' display string."""
    parts = []
    if acn:
        d = "".join(c for c in str(acn) if c.isdigit())
        if len(d) == 9:
            parts.append(f"ACN: {d[:3]} {d[3:6]} {d[6:9]}")
        elif d:
            parts.append(f"ACN: {d}")
    if abn:
        d = "".join(c for c in str(abn) if c.isdigit())
        if len(d) == 11:
            parts.append(f"ABN: {d[:2]} {d[2:5]} {d[5:8]} {d[8:11]}")
        elif d:
            parts.append(f"ABN: {d}")
    return " / ".join(parts) if parts else ""


# ---------------------------------------------------------------------------
# 3. build_company_context
# ---------------------------------------------------------------------------
def build_company_context(financial_year, include_watermark=True):
    """Build full Jinja2 context dict for a company entity."""
    from core.models import EntityOfficer

    fy = financial_year
    entity = fy.entity
    sections = _get_tb_sections(fy)
    has_prior = _has_prior_year(fy)
    has_trading = len(sections["trading_income"]) > 0 or len(sections["cogs"]) > 0

    # P&L calculations
    # Trading income, other income are credit-normal (raw TB value is negative)
    trading_income = _format_lines(sections["trading_income"], credit_normal=True)
    cogs = _format_lines(sections["cogs"])
    income = _format_lines(sections["income"], credit_normal=True)
    expenses = _format_lines(sections["expenses"])

    total_trading_income_cy = -_sum_section(sections["trading_income"])
    total_trading_income_py = -_sum_section(sections["trading_income"], "py_amount")
    total_cogs_cy = _sum_section(sections["cogs"])
    total_cogs_py = _sum_section(sections["cogs"], "py_amount")
    gross_profit_cy = total_trading_income_cy - total_cogs_cy
    gross_profit_py = total_trading_income_py - total_cogs_py

    total_income_cy = -_sum_section(sections["income"])
    total_income_py = -_sum_section(sections["income"], "py_amount")
    total_expenses_cy = _sum_section(sections["expenses"])
    total_expenses_py = _sum_section(sections["expenses"], "py_amount")

    if has_trading:
        net_profit_cy = gross_profit_cy + total_income_cy - total_expenses_cy
        net_profit_py = gross_profit_py + total_income_py - total_expenses_py
    else:
        net_profit_cy = total_income_cy - total_expenses_cy
        net_profit_py = total_income_py - total_expenses_py

    # Pre-closing TB: add current year profit to equity if BS won't balance
    _test_equity = -_sum_section(sections["equity"])
    _test_liab = -(_sum_section(sections["current_liabilities"])
                    + _sum_section(sections["noncurrent_liabilities"]))
    _test_assets = (_sum_section(sections["current_assets"])
                    + _sum_section(sections["noncurrent_assets"]))
    _test_net_assets = _test_assets - _test_liab
    if abs(_test_net_assets - _test_equity) > 1:
        # TB not yet closed — inject current year profit / (loss) line
        sections["equity"].append({
            "account_name": "Current year profit / (loss)",
            "cy_amount": -net_profit_cy,   # credit-normal convention
            "py_amount": -net_profit_py,
        })

    # For P&L rendering: merge trading income & COGS into income & expenses
    # since the P&L template has a single Income and Expenses section
    if has_trading:
        rendered_income = trading_income + income
        rendered_total_income_cy = total_trading_income_cy + total_income_cy
        rendered_total_income_py = total_trading_income_py + total_income_py
        rendered_expenses = cogs + expenses
        rendered_total_expenses_cy = total_cogs_cy + total_expenses_cy
        rendered_total_expenses_py = total_cogs_py + total_expenses_py
    else:
        rendered_income = income
        rendered_total_income_cy = total_income_cy
        rendered_total_income_py = total_income_py
        rendered_expenses = expenses
        rendered_total_expenses_cy = total_expenses_cy
        rendered_total_expenses_py = total_expenses_py

    # Balance Sheet
    current_assets = _format_lines(sections["current_assets"])
    noncurrent_assets = _format_lines(sections["noncurrent_assets"])
    # Liabilities and equity are credit-normal (raw TB value is negative)
    current_liabilities = _format_lines(sections["current_liabilities"], credit_normal=True)
    noncurrent_liabilities = _format_lines(sections["noncurrent_liabilities"], credit_normal=True)
    equity = _format_lines(sections["equity"], credit_normal=True)

    total_current_assets_cy = _sum_section(sections["current_assets"])
    total_current_assets_py = _sum_section(sections["current_assets"], "py_amount")
    total_noncurrent_assets_cy = _sum_section(sections["noncurrent_assets"])
    total_noncurrent_assets_py = _sum_section(sections["noncurrent_assets"], "py_amount")
    total_assets_cy = total_current_assets_cy + total_noncurrent_assets_cy
    total_assets_py = total_current_assets_py + total_noncurrent_assets_py

    total_current_liab_cy = -_sum_section(sections["current_liabilities"])
    total_current_liab_py = -_sum_section(sections["current_liabilities"], "py_amount")
    total_noncurrent_liab_cy = -_sum_section(sections["noncurrent_liabilities"])
    total_noncurrent_liab_py = -_sum_section(sections["noncurrent_liabilities"], "py_amount")
    total_liab_cy = total_current_liab_cy + total_noncurrent_liab_cy
    total_liab_py = total_current_liab_py + total_noncurrent_liab_py

    net_assets_cy = total_assets_cy - total_liab_cy
    net_assets_py = total_assets_py - total_liab_py

    total_equity_cy = -_sum_section(sections["equity"])
    total_equity_py = -_sum_section(sections["equity"], "py_amount")

    # Officers
    directors = EntityOfficer.objects.filter(
        entity=entity,
        role__in=["director"],
        date_ceased__isnull=True,
    ).order_by("display_order", "full_name")

    year_end = fy.end_date
    year_str = str(year_end.year) if year_end else ""
    prior_year_str = str(year_end.year - 1) if year_end else ""
    date_text = f"For the Year Ended {year_end.strftime('%d %B %Y')}" if year_end else ""

    context = {
        "entity_name": entity.entity_name,
        "trading_as": entity.trading_as or "",
        "abn": entity.abn or "",
        "acn": entity.acn or "",
        "acn_abn": _format_acn_abn(entity.acn or "", entity.abn or ""),
        "entity_type": entity.entity_type,
        "year": year_str,
        "financial_year": year_str,
        "prior_year": prior_year_str,
        "date_text": date_text,
        "financial_year_end": year_end.strftime("%d %B %Y") if year_end else "",
        "year_end_date": year_end.strftime("%d %B %Y") if year_end else "",
        "has_prior": has_prior,
        "has_trading": has_trading,
        "watermark": "DRAFT" if include_watermark else "",
        # P&L
        "trading_income": trading_income,
        "cogs": cogs,
        "income": rendered_income,
        "expenses": rendered_expenses,
        "total_trading_income_cy": format_amount(total_trading_income_cy),
        "total_trading_income_py": format_amount(total_trading_income_py),
        "total_cogs_cy": format_amount(total_cogs_cy),
        "total_cogs_py": format_amount(total_cogs_py),
        "gross_profit_cy": format_amount(gross_profit_cy),
        "gross_profit_py": format_amount(gross_profit_py),
        "total_income_cy": format_amount(rendered_total_income_cy),
        "total_income_py": format_amount(rendered_total_income_py),
        "total_expenses_cy": format_amount(rendered_total_expenses_cy),
        "total_expenses_py": format_amount(rendered_total_expenses_py),
        "net_profit_cy": format_amount(net_profit_cy),
        "net_profit_py": format_amount(net_profit_py),
        # Balance Sheet
        "current_assets": current_assets,
        "noncurrent_assets": noncurrent_assets,
        "current_liabilities": current_liabilities,
        "noncurrent_liabilities": noncurrent_liabilities,
        "equity": equity,
        "total_current_assets_cy": format_amount(total_current_assets_cy),
        "total_current_assets_py": format_amount(total_current_assets_py),
        "total_noncurrent_assets_cy": format_amount(total_noncurrent_assets_cy),
        "total_noncurrent_assets_py": format_amount(total_noncurrent_assets_py),
        "total_assets_cy": format_amount(total_assets_cy),
        "total_assets_py": format_amount(total_assets_py),
        "total_current_liab_cy": format_amount(total_current_liab_cy),
        "total_current_liab_py": format_amount(total_current_liab_py),
        "total_noncurrent_liab_cy": format_amount(total_noncurrent_liab_cy),
        "total_noncurrent_liab_py": format_amount(total_noncurrent_liab_py),
        "total_liabilities_cy": format_amount(total_liab_cy),
        "total_liabilities_py": format_amount(total_liab_py),
        "net_assets_cy": format_amount(net_assets_cy),
        "net_assets_py": format_amount(net_assets_py),
        "total_equity_cy": format_amount(total_equity_cy),
        "total_equity_py": format_amount(total_equity_py),
        # Declaration
        "declaration_title": "Directors' Declaration",
        "compilation_responsible_party": "directors",
        "directors": [
            {"name": d.full_name, "title": d.title or "Director"}
            for d in directors
        ],
        # Firm details
        "firm_name": "MC & S Pty Ltd",
        "firm_address_1": "PO Box 4440",
        "firm_address_2": "Dandenong South VIC 3164",
        "firm_phone": "(03) 9794 0000",
        "firm_email": "info@mcands.com.au",
    }

    # Add format_amount as a Jinja2 filter
    context["format_amount"] = format_amount

    return context


# ---------------------------------------------------------------------------
# 4. build_trust_context
# ---------------------------------------------------------------------------
def build_trust_context(financial_year, include_watermark=True):
    """Build full Jinja2 context dict for a trust entity."""
    from core.models import EntityOfficer

    # Start with company context as base (shared structure)
    context = build_company_context(financial_year, include_watermark)

    entity = financial_year.entity

    # Override trust-specific fields
    context["declaration_title"] = "Trustee's Declaration"
    context["compilation_responsible_party"] = "director of the trustee company"

    # Get trustees and beneficiaries
    trustees = EntityOfficer.objects.filter(
        entity=entity,
        role="trustee",
        date_ceased__isnull=True,
    ).order_by("display_order", "full_name")

    beneficiaries = EntityOfficer.objects.filter(
        entity=entity,
        role="beneficiary",
        date_ceased__isnull=True,
    ).order_by("display_order", "full_name")

    context["directors"] = [
        {"name": t.full_name, "title": t.title or "Trustee"}
        for t in trustees
    ]

    # Distribution data
    net_profit_raw = Decimal("0")
    sections = _get_tb_sections(financial_year)
    total_income = -_sum_section(sections["trading_income"]) + -_sum_section(sections["income"])
    total_expenses = _sum_section(sections["expenses"]) + _sum_section(sections["cogs"])
    net_profit_raw = total_income - total_expenses

    distributions = []
    for ben in beneficiaries:
        pct = ben.distribution_percentage or Decimal("0")
        amount = (net_profit_raw * pct / 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        distributions.append({
            "beneficiary_name": ben.full_name,
            "percentage": str(pct),
            "amount": format_amount(amount),
            "amount_raw": amount,
        })

    context["beneficiaries"] = distributions
    context["total_distribution"] = format_amount(net_profit_raw)

    return context


# ---------------------------------------------------------------------------
# 5. build_sole_trader_context
# ---------------------------------------------------------------------------
def build_sole_trader_context(financial_year, include_watermark=True):
    """Build full Jinja2 context dict for a sole trader entity."""
    from core.models import EntityOfficer

    context = build_company_context(financial_year, include_watermark)

    entity = financial_year.entity

    context["declaration_title"] = "Proprietor Declaration"
    context["compilation_responsible_party"] = "owner"

    proprietor = EntityOfficer.objects.filter(
        entity=entity,
        role="sole_trader",
        date_ceased__isnull=True,
    ).first()

    if proprietor:
        context["directors"] = [
            {"name": proprietor.full_name, "title": "Proprietor"}
        ]
    else:
        context["directors"] = [
            {"name": entity.entity_name, "title": "Proprietor"}
        ]

    return context


# ---------------------------------------------------------------------------
# Post-processing — page-break prevention, inline PY fix, ampersand fix
# ---------------------------------------------------------------------------
# Summary row labels that must not split across page breaks.
_SUMMARY_LABELS = [
    "net profit", "net loss", "net profit / (loss)", "net profit/(loss)",
    "total income", "total expenses", "total revenue",
    "total current assets", "total non-current assets", "total assets",
    "total current liabilities", "total non-current liabilities",
    "total liabilities", "net assets", "total equity",
    "gross profit",
]


def _post_process_fs_doc(buffer, doc_type):
    """Post-process a rendered financial statement .docx.

    Applied to ALL document types to fix:
      - Firm-name ampersand stripped by XML rendering (all doc types).
      - cantSplit + keepNext on summary rows (P&L, BS, Summary).
      - keepNext on standalone paragraphs before summary tables (P&L, BS).
      - Inline "(PY: xxx)" values in totals rows (BS).
    """
    import copy
    import re
    from docx import Document
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    doc = Document(buffer)

    # ------------------------------------------------------------------
    # Fix C: Restore ampersand in firm name across ALL runs in document.
    # docxtpl Jinja2 rendering can strip the "&" from "MC & S" because
    # the raw ampersand creates an invalid XML entity reference.
    # ------------------------------------------------------------------
    for paragraph in doc.paragraphs:
        for run in paragraph.runs:
            if run.text and ("MC S" in run.text or "MC  S" in run.text):
                run.text = run.text.replace("MC  S Pty Ltd", "MC & S Pty Ltd")
                run.text = run.text.replace("MC S Pty Ltd", "MC & S Pty Ltd")
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        if run.text and ("MC S" in run.text or "MC  S" in run.text):
                            run.text = run.text.replace("MC  S Pty Ltd", "MC & S Pty Ltd")
                            run.text = run.text.replace("MC S Pty Ltd", "MC & S Pty Ltd")

    # ------------------------------------------------------------------
    # Issues A & B apply only to financial statement body pages.
    # ------------------------------------------------------------------
    if doc_type in ("DETAILED_PL", "BALANCE_SHEET", "SUMMARY_PL"):

        # --- Issue A (part 1): keepNext on standalone paragraphs that
        #     precede a summary table (e.g. the "Net Profit / (Loss)"
        #     heading paragraph that sits above the values table). ---
        body = doc.element.body
        all_elements = list(body)
        for i, el in enumerate(all_elements):
            if el.tag == qn('w:p'):
                para_text = ''.join(
                    t.text or '' for t in el.iter(qn('w:t'))
                ).strip().lower()
                if any(lbl in para_text for lbl in _SUMMARY_LABELS):
                    # Set keepNext so this paragraph stays with the next element
                    pPr = el.find(qn('w:pPr'))
                    if pPr is None:
                        pPr = OxmlElement('w:pPr')
                        el.insert(0, pPr)
                    for existing in pPr.findall(qn('w:keepNext')):
                        pPr.remove(existing)
                    keepNext = OxmlElement('w:keepNext')
                    keepNext.set(qn('w:val'), '1')
                    pPr.append(keepNext)

            # Also set keepNext on section heading paragraphs ("Income",
            # "Expenses", "Current Assets", etc.) that precede tables.
            if el.tag == qn('w:p') and i + 1 < len(all_elements):
                next_el = all_elements[i + 1]
                if next_el.tag == qn('w:tbl'):
                    para_text = ''.join(
                        t.text or '' for t in el.iter(qn('w:t'))
                    ).strip()
                    if para_text and len(para_text) < 40:
                        pPr = el.find(qn('w:pPr'))
                        if pPr is None:
                            pPr = OxmlElement('w:pPr')
                            el.insert(0, pPr)
                        if pPr.find(qn('w:keepNext')) is None:
                            keepNext = OxmlElement('w:keepNext')
                            keepNext.set(qn('w:val'), '1')
                            pPr.append(keepNext)

        # --- Issue A (part 2) + Issue B: process table rows ---
        for table in doc.tables:
            for row in table.rows:
                first_cell_text = row.cells[0].text.strip().lower() if row.cells else ""

                # cantSplit + keepNext on summary rows
                is_summary = any(label in first_cell_text for label in _SUMMARY_LABELS)
                if is_summary:
                    tr = row._tr
                    trPr = tr.get_or_add_trPr()
                    for existing in trPr.findall(qn('w:cantSplit')):
                        trPr.remove(existing)
                    cantSplit = OxmlElement('w:cantSplit')
                    cantSplit.set(qn('w:val'), '1')
                    trPr.append(cantSplit)
                    # Also set keepNext on paragraphs in the row
                    for cell in row.cells:
                        for para in cell.paragraphs:
                            ppPr = para._p.get_or_add_pPr()
                            if ppPr.find(qn('w:keepNext')) is None:
                                kn = OxmlElement('w:keepNext')
                                kn.set(qn('w:val'), '1')
                                ppPr.append(kn)

                # Fix inline PY values in Balance Sheet totals
                if doc_type == "BALANCE_SHEET":
                    _fix_inline_py_in_row(row, qn, OxmlElement, copy, re)

    output = io.BytesIO()
    doc.save(output)
    output.seek(0)
    return output


def _fix_inline_py_in_row(row, qn, OxmlElement, copy, re):
    """Split a merged cell containing 'CY (PY: xxx)' into separate CY and PY cells."""
    tr = row._tr
    tcs = list(tr.findall(qn('w:tc')))

    for tc in tcs:
        # Collect all text from the cell
        text = ''.join(t.text or '' for t in tc.iter(qn('w:t')))
        match = re.search(r'\(PY:\s*(.+?)\)\s*$', text)
        if not match:
            continue

        py_text = match.group(1).strip()
        cy_text = text[:match.start()].strip()

        # Check for gridSpan (merged cell)
        tcPr = tc.find(qn('w:tcPr'))
        if tcPr is not None:
            gridSpan = tcPr.find(qn('w:gridSpan'))
            if gridSpan is not None:
                span = int(gridSpan.get(qn('w:val'), '1'))
                if span > 1:
                    if span - 1 > 1:
                        gridSpan.set(qn('w:val'), str(span - 1))
                    else:
                        tcPr.remove(gridSpan)

        # Set this cell's text to CY value only
        _set_cell_text(tc, cy_text, qn)

        # Create new cell for PY value (clone formatting from CY cell)
        new_tc = copy.deepcopy(tc)
        # Remove gridSpan from new cell
        new_tcPr = new_tc.find(qn('w:tcPr'))
        if new_tcPr is not None:
            new_gs = new_tcPr.find(qn('w:gridSpan'))
            if new_gs is not None:
                new_tcPr.remove(new_gs)

        _set_cell_text(new_tc, py_text, qn)
        tc.addnext(new_tc)
        break  # Only fix one cell per row


def _set_cell_text(tc, text, qn):
    """Set the text of all runs in a table cell, preserving formatting."""
    first_set = False
    for p in tc.findall(qn('w:p')):
        for r in p.findall(qn('w:r')):
            for t in r.findall(qn('w:t')):
                if not first_set:
                    t.text = text
                    first_set = True
                else:
                    t.text = ''
    if not first_set:
        # No runs found — create one
        p_els = tc.findall(qn('w:p'))
        if p_els:
            r_el = OxmlElement('w:r')
            t_el = OxmlElement('w:t')
            t_el.text = text
            r_el.append(t_el)
            p_els[0].append(r_el)


# ---------------------------------------------------------------------------
# 6. render_template
# ---------------------------------------------------------------------------
def render_template(template_db_record, context):
    """Load .docx via DocxTemplate, render with Jinja2 context, return BytesIO."""
    from docxtpl import DocxTemplate

    if template_db_record is None:
        raise ValueError("render_template called with None template record")

    if not template_db_record.template_file:
        raise ValueError(
            f"Template file not found for {template_db_record.document_type} / "
            f"{template_db_record.entity_type}: FileField is empty "
            f"(record pk={template_db_record.pk})"
        )

    try:
        template_path = template_db_record.template_file.path
    except ValueError:
        raise ValueError(
            f"Template file not found for {template_db_record.document_type} / "
            f"{template_db_record.entity_type}: no file associated with FileField "
            f"(record pk={template_db_record.pk})"
        )

    if not os.path.exists(template_path):
        raise ValueError(
            f"Template file not found for {template_db_record.document_type} / "
            f"{template_db_record.entity_type}: {template_path} does not exist on disk "
            f"(record pk={template_db_record.pk})"
        )

    tpl = DocxTemplate(template_path)

    tpl.render(context)

    buffer = io.BytesIO()
    tpl.save(buffer)
    buffer.seek(0)
    return buffer


# ---------------------------------------------------------------------------
# 7. generate_financial_statements
# ---------------------------------------------------------------------------
DOCUMENT_TYPE_ORDER = [
    "COVER",
    "COMPILATION",
    "DETAILED_PL",
    "BALANCE_SHEET",
    "SUMMARY_PL",
    "NOTES",
    "DECLARATION",
    "DISTRIBUTION",
]


def generate_financial_statements(financial_year_id, include_watermark=True):
    """Orchestrate all templates for a financial year.

    Returns dict of document_type → BytesIO.
    """
    from core.models import FinancialStatementTemplate, FinancialYear

    fy = FinancialYear.objects.select_related(
        "entity", "entity__client", "prior_year",
    ).get(pk=financial_year_id)
    entity = fy.entity
    entity_type = entity.entity_type

    # Build context based on entity type
    context_builders = {
        "company": build_company_context,
        "trust": build_trust_context,
        "sole_trader": build_sole_trader_context,
    }
    builder = context_builders.get(entity_type, build_company_context)
    context = builder(fy, include_watermark=include_watermark)

    # Get active templates for this entity type
    templates = FinancialStatementTemplate.objects.filter(
        entity_type=entity_type,
        is_active=True,
    )

    # Skip SUMMARY_PL for non-company entities
    # Skip DISTRIBUTION for non-trust entities
    skip_types = set()
    if entity_type != "company":
        skip_types.add("SUMMARY_PL")
    if entity_type != "trust":
        skip_types.add("DISTRIBUTION")

    results = {}
    for doc_type in DOCUMENT_TYPE_ORDER:
        if doc_type in skip_types:
            continue

        tmpl = templates.filter(document_type=doc_type).first()
        if not tmpl:
            logger.warning(
                "No active template for %s/%s — skipping",
                doc_type, entity_type,
            )
            continue

        try:
            buffer = render_template(tmpl, context)
            buffer = _post_process_fs_doc(buffer, doc_type)
            results[doc_type] = buffer
            logger.info("Rendered %s for FY %s", doc_type, fy.pk)
        except Exception as e:
            logger.error(
                "Failed to render %s for FY %s: %s", doc_type, fy.pk, e
            )

    return results


# ---------------------------------------------------------------------------
# generate_combined_pdf — render each template to PDF individually, merge
# ---------------------------------------------------------------------------
def generate_combined_pdf(financial_year_id, include_watermark=True, exclude_types=None):
    """Generate all templates, convert each to PDF, merge into single PDF BytesIO.

    Args:
        exclude_types: optional set of document type keys to skip (e.g. {"DECLARATION"})

    Returns a BytesIO containing the merged PDF bytes.
    """
    from pypdf import PdfWriter, PdfReader

    logger.info("generate_combined_pdf called for FY %s (watermark=%s)",
                financial_year_id, include_watermark)

    docs = generate_financial_statements(financial_year_id, include_watermark)

    logger.info("generate_financial_statements returned %d documents: %s",
                len(docs), list(docs.keys()))

    if not docs:
        raise RuntimeError("No templates rendered — check template registration")

    excluded = exclude_types or set()
    ordered_keys = [dt for dt in DOCUMENT_TYPE_ORDER if dt in docs and dt not in excluded]
    logger.info("Ordered keys for PDF merge: %s", ordered_keys)

    if not ordered_keys:
        raise RuntimeError("No templates rendered")

    writer = PdfWriter()
    tmpdir = tempfile.mkdtemp(prefix="shub_combined_pdf_")
    pdfs_merged = 0

    try:
        for doc_type in ordered_keys:
            buffer = docs[doc_type]
            docx_path = os.path.join(tmpdir, f"{doc_type}.docx")
            with open(docx_path, "wb") as f:
                f.write(buffer.read())

            try:
                convert_docx_to_pdf(docx_path, tmpdir, timeout=60)
            except RuntimeError:
                logger.error("LibreOffice not available — skipping %s", doc_type)
                continue

            pdf_path = os.path.join(tmpdir, f"{doc_type}.pdf")
            if os.path.exists(pdf_path):
                reader = PdfReader(pdf_path)
                for page in reader.pages:
                    writer.add_page(page)
                pdfs_merged += 1
                logger.info("Merged %s into combined PDF (%d pages)", doc_type, len(reader.pages))
            else:
                logger.warning("PDF conversion produced no output for %s", doc_type)

        if pdfs_merged == 0:
            raise RuntimeError("No templates could be converted to PDF")

        output = io.BytesIO()
        writer.write(output)
        output.seek(0)
        logger.info("generate_combined_pdf complete: %d documents, %d bytes",
                    pdfs_merged, output.getbuffer().nbytes)
        return output
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 8. assemble_pdf_package
# ---------------------------------------------------------------------------
def assemble_pdf_package(financial_year_id):
    """Generate all docs with include_watermark=False, convert to PDF, merge.

    Returns bytes of the merged PDF.
    """
    docs = generate_financial_statements(
        financial_year_id, include_watermark=False,
    )

    if not docs:
        logger.warning("No documents generated for FY %s", financial_year_id)
        return None

    try:
        from PyPDF2 import PdfMerger
    except ImportError:
        logger.error("PyPDF2 not available — cannot merge PDFs")
        return None

    merger = PdfMerger()
    tmpdir = tempfile.mkdtemp(prefix="shub_fs_pkg_")
    pdfs_added = 0

    for doc_type in DOCUMENT_TYPE_ORDER:
        if doc_type not in docs:
            continue

        buffer = docs[doc_type]
        docx_path = os.path.join(tmpdir, f"{doc_type}.docx")
        with open(docx_path, "wb") as f:
            f.write(buffer.read())

        # Convert to PDF via LibreOffice
        pdf_path = os.path.join(tmpdir, f"{doc_type}.pdf")
        try:
            convert_docx_to_pdf(docx_path, tmpdir, timeout=60)
        except RuntimeError:
            logger.error("LibreOffice not available — skipping PDF conversion for %s", doc_type)
            continue

        if os.path.exists(pdf_path):
            try:
                merger.append(pdf_path)
                pdfs_added += 1
            except Exception as e:
                logger.error("Failed to append PDF %s: %s", doc_type, e)
        else:
            logger.warning("PDF conversion produced no output for %s", doc_type)

    if pdfs_added == 0:
        merger.close()
        return None

    output = io.BytesIO()
    merger.write(output)
    merger.close()
    output.seek(0)

    logger.info(
        "Assembled PDF package for FY %s: %d documents",
        financial_year_id, pdfs_added,
    )
    return output.read()
