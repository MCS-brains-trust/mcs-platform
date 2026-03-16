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
import subprocess
import tempfile
from collections import OrderedDict
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings

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
        cy = line.debit - line.credit
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

        cy = line.debit - line.credit
        py = line.prior_debit - line.prior_credit
        entry = {
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

    # Aggregate within each section
    for key in sections:
        raw = sections[key]
        if not raw:
            continue
        agg = OrderedDict()
        name_counts = {}
        for entry in raw:
            norm = entry["account_name"].strip().lower()
            if norm in agg:
                agg[norm]["cy_amount"] += entry["cy_amount"]
                agg[norm]["py_amount"] += entry["py_amount"]
                name_counts[norm][entry["account_name"]] = (
                    name_counts[norm].get(entry["account_name"], 0) + 1
                )
            else:
                agg[norm] = {
                    "account_name": entry["account_name"],
                    "cy_amount": entry["cy_amount"],
                    "py_amount": entry["py_amount"],
                }
                name_counts[norm] = {entry["account_name"]: 1}
        for norm in agg:
            best = max(name_counts[norm], key=name_counts[norm].get)
            agg[norm]["account_name"] = best
        sections[key] = list(agg.values())
        logger.debug(
            "_get_tb_sections [%s]: %d raw -> %d aggregated",
            key, len(raw), len(agg),
        )

    return sections


def _sum_section(items, field="cy_amount"):
    """Sum a list of dicts by field."""
    return sum(item[field] for item in items)


def _format_lines(items):
    """Add formatted amount strings to each item dict."""
    for item in items:
        item["cy_formatted"] = format_amount(item["cy_amount"])
        item["py_formatted"] = format_amount(item["py_amount"])
    return items


def _has_prior_year(fy):
    """Check if there is prior year data."""
    if not fy.prior_year:
        return False
    return fy.prior_year.trial_balance_lines.exists()


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
    has_trading = len(sections["cogs"]) > 0

    # P&L calculations
    trading_income = _format_lines(sections["trading_income"])
    cogs = _format_lines(sections["cogs"])
    income = _format_lines(sections["income"])
    expenses = _format_lines(sections["expenses"])

    total_trading_income_cy = abs(_sum_section(sections["trading_income"]))
    total_trading_income_py = abs(_sum_section(sections["trading_income"], "py_amount"))
    total_cogs_cy = _sum_section(sections["cogs"])
    total_cogs_py = _sum_section(sections["cogs"], "py_amount")
    gross_profit_cy = total_trading_income_cy - total_cogs_cy
    gross_profit_py = total_trading_income_py - total_cogs_py

    total_income_cy = abs(_sum_section(sections["income"]))
    total_income_py = abs(_sum_section(sections["income"], "py_amount"))
    total_expenses_cy = _sum_section(sections["expenses"])
    total_expenses_py = _sum_section(sections["expenses"], "py_amount")

    if has_trading:
        net_profit_cy = gross_profit_cy + total_income_cy - total_expenses_cy
        net_profit_py = gross_profit_py + total_income_py - total_expenses_py
    else:
        net_profit_cy = total_income_cy - total_expenses_cy
        net_profit_py = total_income_py - total_expenses_py

    # Balance Sheet
    current_assets = _format_lines(sections["current_assets"])
    noncurrent_assets = _format_lines(sections["noncurrent_assets"])
    current_liabilities = _format_lines(sections["current_liabilities"])
    noncurrent_liabilities = _format_lines(sections["noncurrent_liabilities"])
    equity = _format_lines(sections["equity"])

    total_current_assets_cy = _sum_section(sections["current_assets"])
    total_current_assets_py = _sum_section(sections["current_assets"], "py_amount")
    total_noncurrent_assets_cy = _sum_section(sections["noncurrent_assets"])
    total_noncurrent_assets_py = _sum_section(sections["noncurrent_assets"], "py_amount")
    total_assets_cy = total_current_assets_cy + total_noncurrent_assets_cy
    total_assets_py = total_current_assets_py + total_noncurrent_assets_py

    total_current_liab_cy = abs(_sum_section(sections["current_liabilities"]))
    total_current_liab_py = abs(_sum_section(sections["current_liabilities"], "py_amount"))
    total_noncurrent_liab_cy = abs(_sum_section(sections["noncurrent_liabilities"]))
    total_noncurrent_liab_py = abs(_sum_section(sections["noncurrent_liabilities"], "py_amount"))
    total_liab_cy = total_current_liab_cy + total_noncurrent_liab_cy
    total_liab_py = total_current_liab_py + total_noncurrent_liab_py

    net_assets_cy = total_assets_cy - total_liab_cy
    net_assets_py = total_assets_py - total_liab_py

    total_equity_cy = abs(_sum_section(sections["equity"]))
    total_equity_py = abs(_sum_section(sections["equity"], "py_amount"))

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
        "entity_type": entity.entity_type,
        "year": year_str,
        "prior_year": prior_year_str,
        "date_text": date_text,
        "year_end_date": year_end.strftime("%d %B %Y") if year_end else "",
        "has_prior": has_prior,
        "has_trading": has_trading,
        "watermark": "DRAFT" if include_watermark else "",
        # P&L
        "trading_income": trading_income,
        "cogs": cogs,
        "income": income,
        "expenses": expenses,
        "total_trading_income_cy": format_amount(total_trading_income_cy),
        "total_trading_income_py": format_amount(total_trading_income_py),
        "total_cogs_cy": format_amount(total_cogs_cy),
        "total_cogs_py": format_amount(total_cogs_py),
        "gross_profit_cy": format_amount(gross_profit_cy),
        "gross_profit_py": format_amount(gross_profit_py),
        "total_income_cy": format_amount(total_income_cy),
        "total_income_py": format_amount(total_income_py),
        "total_expenses_cy": format_amount(total_expenses_cy),
        "total_expenses_py": format_amount(total_expenses_py),
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
        "firm_name": "M C & S Pty Ltd",
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
    total_income = abs(_sum_section(sections["trading_income"])) + abs(_sum_section(sections["income"]))
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
# 6. render_template
# ---------------------------------------------------------------------------
def render_template(template_db_record, context):
    """Load .docx via DocxTemplate, render with Jinja2 context, return BytesIO."""
    from docxtpl import DocxTemplate

    template_path = template_db_record.template_file.path
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template file not found: {template_path}")

    doc = DocxTemplate(template_path)

    # Register format_amount as a Jinja2 filter
    doc.jinja_env.filters["format_amount"] = format_amount

    doc.render(context)

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer


# ---------------------------------------------------------------------------
# 7. generate_financial_statements
# ---------------------------------------------------------------------------
DOCUMENT_TYPE_ORDER = [
    "COVER",
    "DETAILED_PL",
    "BALANCE_SHEET",
    "SUMMARY_PL",
    "NOTES",
    "DECLARATION",
    "COMPILATION",
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
            results[doc_type] = buffer
            logger.info("Rendered %s for FY %s", doc_type, fy.pk)
        except Exception as e:
            logger.error(
                "Failed to render %s for FY %s: %s", doc_type, fy.pk, e
            )

    return results


# ---------------------------------------------------------------------------
# generate_combined_docx — view-facing helper
# ---------------------------------------------------------------------------
def generate_combined_docx(financial_year_id, include_watermark=True):
    """Generate all templates and combine into a single DOCX BytesIO.

    This is the drop-in replacement for the old docgen.generate_financial_statements.
    The old function returned a single BytesIO; this does the same by appending
    each rendered template's body elements into a single Word document.
    """
    from docx import Document as DocxDocument

    docs = generate_financial_statements(financial_year_id, include_watermark)

    if not docs:
        raise RuntimeError("No templates rendered — check template registration")

    # Start with the first document as the base
    ordered_keys = [dt for dt in DOCUMENT_TYPE_ORDER if dt in docs]
    if not ordered_keys:
        raise RuntimeError("No templates rendered")

    first_key = ordered_keys[0]
    combined = DocxDocument(docs[first_key])

    # Append remaining documents
    for key in ordered_keys[1:]:
        sub_doc = DocxDocument(docs[key])
        # Add a page break before appending
        combined.add_page_break()
        for element in sub_doc.element.body:
            combined.element.body.append(element)

    buffer = io.BytesIO()
    combined.save(buffer)
    buffer.seek(0)
    return buffer


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
            subprocess.run(
                [
                    "libreoffice", "--headless", "--convert-to", "pdf",
                    "--outdir", tmpdir, docx_path,
                ],
                capture_output=True,
                timeout=60,
            )
        except FileNotFoundError:
            try:
                subprocess.run(
                    [
                        "soffice", "--headless", "--convert-to", "pdf",
                        "--outdir", tmpdir, docx_path,
                    ],
                    capture_output=True,
                    timeout=60,
                )
            except FileNotFoundError:
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
