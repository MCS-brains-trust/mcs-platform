"""
Management command: generate_fs_templates

Builds default .docx template files using docxtpl-compatible Jinja2 merge
fields and registers them in the FinancialStatementTemplate model.

Usage:
    python3 manage.py generate_fs_templates
    python3 manage.py generate_fs_templates --force   # overwrite existing
"""
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from docx import Document
from docx.shared import Inches, Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

from core.models import FinancialStatementTemplate


# Template layout constants from DOCGEN.md
FONT_NAME = "Calibri"
FONT_SIZE = Pt(10)
PAGE_MARGIN_TOP = Cm(2)
PAGE_MARGIN_BOTTOM = Cm(2)
PAGE_MARGIN_LEFT = Cm(2.5)
PAGE_MARGIN_RIGHT = Cm(2)

# Column widths for 4-column table: name(8.5cm) note(1.5cm) cy(3cm) py(3cm)
COL_WIDTHS = [Cm(8.5), Cm(1.5), Cm(3), Cm(3)]

# Entity types that get templates
ENTITY_TYPES = ["company", "trust", "sole_trader", "partnership"]

# Document types — Compilation Report last per APES 315
DOC_TYPES = [
    ("COVER", "Cover Page"),
    ("DETAILED_PL", "Detailed Profit and Loss Statement"),
    ("BALANCE_SHEET", "Detailed Balance Sheet"),
    ("SUMMARY_PL", "Summary P&L"),
    ("NOTES", "Notes to Financial Statements"),
    ("DECLARATION", "Declaration"),
    ("DISTRIBUTION", "Distribution Summary"),
    ("COMPILATION", "Compilation Report"),
]

# Which doc types apply to which entity types
ENTITY_DOC_TYPES = {
    "company": ["COVER", "DETAILED_PL", "BALANCE_SHEET", "SUMMARY_PL",
                 "NOTES", "DECLARATION", "COMPILATION"],
    "trust": ["COVER", "DETAILED_PL", "BALANCE_SHEET", "NOTES",
              "DECLARATION", "COMPILATION", "DISTRIBUTION"],
    "sole_trader": ["COVER", "DETAILED_PL", "BALANCE_SHEET", "NOTES",
                     "DECLARATION", "COMPILATION"],
    "partnership": ["COVER", "DETAILED_PL", "BALANCE_SHEET", "NOTES",
                     "DECLARATION", "COMPILATION"],
}


def _set_page_setup(doc):
    """Set A4 portrait margins per DOCGEN.md spec."""
    for section in doc.sections:
        section.top_margin = PAGE_MARGIN_TOP
        section.bottom_margin = PAGE_MARGIN_BOTTOM
        section.left_margin = PAGE_MARGIN_LEFT
        section.right_margin = PAGE_MARGIN_RIGHT


def _set_default_font(doc):
    """Set Calibri 10pt as default font."""
    style = doc.styles["Normal"]
    style.font.name = FONT_NAME
    style.font.size = FONT_SIZE


def _add_para(doc, text, bold=False, italic=False, alignment=WD_ALIGN_PARAGRAPH.LEFT,
              size=None, color=None, keep_with_next=False):
    """Add a styled paragraph."""
    p = doc.add_paragraph()
    p.alignment = alignment
    run = p.add_run(text)
    run.font.name = FONT_NAME
    run.font.size = size or FONT_SIZE
    run.bold = bold
    run.font.italic = italic
    if color:
        run.font.color.rgb = color
    if keep_with_next:
        pPr = p._p.get_or_add_pPr()
        keepNext = OxmlElement('w:keepNext')
        keepNext.set(qn('w:val'), '1')
        pPr.append(keepNext)
    return p


def _set_table_full_width(table):
    """Set a document-body table width to full page text width (9356 twips = 16cm)."""
    tbl = table._tbl
    tblPr = tbl.tblPr
    tblW = OxmlElement('w:tblW')
    tblW.set(qn('w:w'), '9356')
    tblW.set(qn('w:type'), 'dxa')
    tblPr.append(tblW)


# ---------------------------------------------------------------------------
# Border helpers — Australian special-purpose FS presentation
# ---------------------------------------------------------------------------
def _apply_cell_border(cell, **kwargs):
    """Apply borders to a cell. kwargs: top, bottom with {val, sz, color} dicts."""
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcBorders = tcPr.find(qn('w:tcBorders'))
    if tcBorders is None:
        tcBorders = OxmlElement('w:tcBorders')
        tcPr.append(tcBorders)
    for edge, attrs in kwargs.items():
        el = tcBorders.find(qn(f'w:{edge}'))
        if el is None:
            el = OxmlElement(f'w:{edge}')
            tcBorders.append(el)
        el.set(qn('w:val'), attrs.get('val', 'single'))
        el.set(qn('w:sz'), str(attrs.get('sz', 6)))
        el.set(qn('w:space'), '0')
        el.set(qn('w:color'), attrs.get('color', '000000'))


def _apply_subtotal_borders(row, amount_col_indices=None):
    """Subtotal row: single thin top + single thin bottom on ALL cells. Bold text."""
    for cell in row.cells:
        _apply_cell_border(
            cell,
            top={"val": "single", "sz": "6", "color": "000000"},
            bottom={"val": "single", "sz": "6", "color": "000000"},
        )
    for cell in row.cells:
        for para in cell.paragraphs:
            for run in para.runs:
                run.bold = True


def _apply_grand_total_borders(row, amount_col_indices=None):
    """Grand total row: single thin top + double bottom on ALL cells. Bold text."""
    for cell in row.cells:
        _apply_cell_border(
            cell,
            top={"val": "single", "sz": "6", "color": "000000"},
            bottom={"val": "double", "sz": "12", "color": "000000"},
        )
    for cell in row.cells:
        for para in cell.paragraphs:
            for run in para.runs:
                run.bold = True


# ---------------------------------------------------------------------------
# Page number footer helper
# ---------------------------------------------------------------------------
def _add_page_number_footer(doc):
    """Placeholder — page numbers are stamped on the final merged PDF.

    This sets up a minimal footer so the section knows it has one,
    but does NOT embed a PAGE field (those restart per-document).
    """
    section = doc.sections[0]
    footer = section.footer
    footer.is_linked_to_previous = False
    # Empty footer — page number will be stamped by _stamp_page_numbers()
    p = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    p.text = ""


def _add_total_row(doc, label, cy_tag, py_tag, size=None, grand_total=False):
    """Add a single-row 4-column table for a total/summary line (label, note, CY, PY)."""
    font_size = size or FONT_SIZE
    table = doc.add_table(rows=1, cols=4)
    _set_table_full_width(table)
    table.autofit = False
    for i, width in enumerate(COL_WIDTHS):
        table.columns[i].width = width
    row = table.rows[0]
    # Prevent this summary row from splitting across pages
    tr = row._tr
    trPr = tr.get_or_add_trPr()
    cantSplit = OxmlElement('w:cantSplit')
    cantSplit.set(qn('w:val'), '1')
    trPr.append(cantSplit)
    row.cells[0].text = label
    row.cells[1].text = ""
    row.cells[2].text = cy_tag
    row.cells[3].text = py_tag
    for i in range(4):
        for p in row.cells[i].paragraphs:
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if i >= 2 else WD_ALIGN_PARAGRAPH.LEFT
            for run in p.runs:
                run.font.name = FONT_NAME
                run.font.size = font_size
                run.bold = True
    # Fix 5: grand total borders (single top + double bottom on amount cells)
    _apply_grand_total_borders(row, [2, 3])


def _add_repeating_header(doc, document_title, date_field="{{ date_text }}"):
    """Add a repeating page header: entity name, ABN, doc title, date, horizontal rule.

    Uses Jinja2 variables rendered by docxtpl. Repeats on every page.
    Also includes the DRAFT watermark (hidden when context is empty).
    """
    section = doc.sections[0]
    section.different_first_page_header_footer = False
    header = section.header
    header.is_linked_to_previous = False

    # Clear any existing header content
    for para in list(header.paragraphs):
        para.clear()

    # Entity name — bold, 11pt, left-aligned
    p1 = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    p1.text = ""
    p1.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = p1.add_run("{{ entity_name }}")
    run.font.name = FONT_NAME
    run.font.size = Pt(11)
    run.bold = True
    p1.paragraph_format.space_after = Pt(0)
    p1.paragraph_format.space_before = Pt(0)

    # ABN — 9pt
    p2 = header.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run2 = p2.add_run("ABN {{ abn }}")
    run2.font.name = FONT_NAME
    run2.font.size = Pt(9)
    p2.paragraph_format.space_after = Pt(0)
    p2.paragraph_format.space_before = Pt(0)

    # Document title — 9pt
    p3 = header.add_paragraph()
    p3.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run3 = p3.add_run(document_title)
    run3.font.name = FONT_NAME
    run3.font.size = Pt(9)
    p3.paragraph_format.space_after = Pt(0)
    p3.paragraph_format.space_before = Pt(0)

    # Date / period — 9pt, with bottom border (horizontal rule)
    p4 = header.add_paragraph()
    p4.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run4 = p4.add_run(date_field)
    run4.font.name = FONT_NAME
    run4.font.size = Pt(9)
    p4.paragraph_format.space_after = Pt(4)
    p4.paragraph_format.space_before = Pt(0)
    # Horizontal rule — bottom border on this paragraph
    pPr = p4._p.get_or_add_pPr()
    pBdr = OxmlElement('w:pBdr')
    bottom_border = OxmlElement('w:bottom')
    bottom_border.set(qn('w:val'), 'single')
    bottom_border.set(qn('w:sz'), '6')
    bottom_border.set(qn('w:space'), '1')
    bottom_border.set(qn('w:color'), '000000')
    pBdr.append(bottom_border)
    pPr.append(pBdr)

    # DRAFT watermark — right-aligned, red, only visible when non-empty
    pw = header.add_paragraph()
    pw.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    runw = pw.add_run("{{ watermark }}")
    runw.font.name = FONT_NAME
    runw.font.size = Pt(14)
    runw.font.color.rgb = RGBColor(0xFF, 0x00, 0x00)
    runw.bold = True
    pw.paragraph_format.space_after = Pt(0)
    pw.paragraph_format.space_before = Pt(0)


def _add_footer(doc, text="These financial statements are unaudited. They must be read in conjunction with the attached Accountant\u2019s Compilation Report and Notes which form part of these financial statements."):
    """Add standard footer with full unaudited disclaimer text.

    Page numbers are NOT added here — they are stamped on the final
    merged PDF by _stamp_page_numbers() so numbering runs continuously
    across the entire assembled package.
    """
    section = doc.sections[0]
    footer = section.footer
    footer.is_linked_to_previous = False

    p = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    p.text = ""
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = p.add_run(text)
    run.font.name = FONT_NAME
    run.font.size = Pt(8)
    run.font.italic = True


def _add_financial_table(doc, section_title, items_tag, total_label, total_cy_tag, total_py_tag):
    """Add a 4-column financial table with Jinja2 for-loop."""
    _add_para(doc, section_title, bold=True, keep_with_next=True)

    table = doc.add_table(rows=1, cols=4)
    _set_table_full_width(table)
    table.autofit = False
    for i, width in enumerate(COL_WIDTHS):
        table.columns[i].width = width

    # Header row
    hdr = table.rows[0]
    hdr.cells[0].text = ""
    hdr.cells[1].text = "Note"
    hdr.cells[2].text = "{{ year }}\n$"
    hdr.cells[3].text = "{{ prior_year }}\n$"
    for i in range(4):
        for p in hdr.cells[i].paragraphs:
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if i >= 2 else WD_ALIGN_PARAGRAPH.LEFT
            for run in p.runs:
                run.font.name = FONT_NAME
                run.font.size = FONT_SIZE
                run.bold = True

    # Fix 7a: Mark header row to repeat on each page (tblHeader)
    tr = hdr._tr
    trPr = tr.get_or_add_trPr()
    tblHeader = OxmlElement('w:tblHeader')
    trPr.append(tblHeader)
    # Fix 7a: Keep header with first data row
    for cell in hdr.cells:
        for p in cell.paragraphs:
            pPr = p._p.get_or_add_pPr()
            kn = OxmlElement('w:keepNext')
            kn.set(qn('w:val'), '1')
            pPr.append(kn)

    # Row 1 — {%tr for %} tag in its own row (docxtpl requirement)
    for_row = table.add_row()
    for_row.cells[0].text = "{%tr for item in " + items_tag + " %}"

    # Row 2 — data row with item fields
    data_row = table.add_row()
    data_row.cells[0].text = "{{ item.account_name }}"
    data_row.cells[1].text = ""
    data_row.cells[2].text = "{{ item.cy_formatted }}"
    data_row.cells[3].text = "{{ item.py_formatted }}"
    for i in range(4):
        for p in data_row.cells[i].paragraphs:
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if i >= 2 else WD_ALIGN_PARAGRAPH.LEFT
            for run in p.runs:
                run.font.name = FONT_NAME
                run.font.size = FONT_SIZE

    # Row 3 — {%tr endfor %} tag in its own row
    endfor_row = table.add_row()
    endfor_row.cells[0].text = "{%tr endfor %}"

    # Total row — subtotal borders (single top + single bottom on amount cells)
    total_row = table.add_row()
    total_row.cells[0].text = total_label
    total_row.cells[2].text = total_cy_tag
    total_row.cells[3].text = total_py_tag
    for i in range(4):
        for p in total_row.cells[i].paragraphs:
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if i >= 2 else WD_ALIGN_PARAGRAPH.LEFT
            for run in p.runs:
                run.font.name = FONT_NAME
                run.font.size = FONT_SIZE
                run.bold = True
    # Fix 5: subtotal borders on amount cells
    _apply_subtotal_borders(total_row, [2, 3])

    return table


# ---------------------------------------------------------------------------
# Template builders
# ---------------------------------------------------------------------------
def _build_cover(entity_type):
    """Build cover page template — first page of the assembled package."""
    doc = Document()
    _set_default_font(doc)
    _set_page_setup(doc)

    # No repeating header on cover page — leave header empty
    section = doc.sections[0]
    header = section.header
    header.is_linked_to_previous = False
    p = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    p.text = ""

    # --- Logo at top (if file exists) ---
    import os as _os
    logo_candidates = [
        _os.path.join(settings.BASE_DIR, "static", "img", "mcs_logo.png"),
        _os.path.join(settings.BASE_DIR, "static", "MCSlogo.png"),
    ]
    logo_path = None
    for candidate in logo_candidates:
        if _os.path.isfile(candidate):
            logo_path = candidate
            break

    _add_para(doc, "", size=Pt(20))  # small top spacer
    if logo_path:
        p_logo = doc.add_paragraph()
        p_logo.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run_logo = p_logo.add_run()
        run_logo.add_picture(logo_path, width=Cm(4))

    # --- Entity details ---
    _add_para(doc, "", size=Pt(30))  # spacer after logo

    _add_para(doc, "{{ entity_name }}", bold=True, size=Pt(18),
              alignment=WD_ALIGN_PARAGRAPH.CENTER)
    if entity_type in ("company",):
        _add_para(doc, "ACN {{ acn }}", size=Pt(11),
                  alignment=WD_ALIGN_PARAGRAPH.CENTER)
    _add_para(doc, "ABN {{ abn }}", size=Pt(11),
              alignment=WD_ALIGN_PARAGRAPH.CENTER)

    _add_para(doc, "", size=Pt(24))  # spacer

    _add_para(doc, "Financial Statements", size=Pt(11),
              alignment=WD_ALIGN_PARAGRAPH.CENTER)
    _add_para(doc, "{{ date_text }}", size=Pt(11),
              alignment=WD_ALIGN_PARAGRAPH.CENTER)

    # --- Contents ---
    _add_para(doc, "", size=Pt(30))  # spacer
    _add_para(doc, "Contents", bold=True, size=Pt(14),
              alignment=WD_ALIGN_PARAGRAPH.LEFT)

    contents = [
        "Detailed Profit and Loss Statement",
        "Detailed Balance Sheet",
    ]
    if entity_type == "company":
        contents.append("Summary Profit and Loss Statement")
    contents.append("Notes to the Financial Statements")
    if entity_type == "company":
        contents.append("Directors' Declaration")
    elif entity_type == "trust":
        contents.append("Trustee's Declaration")
        contents.append("Beneficiaries Distribution Summary")
    elif entity_type == "sole_trader":
        contents.append("Proprietor Declaration")
    elif entity_type == "partnership":
        contents.append("Partners' Declaration")
    contents.append("Compilation Report")

    for i, item in enumerate(contents, 1):
        _add_para(doc, f"{i}.\t{item}", size=Pt(11))

    # --- Push firm details to bottom of page ---
    # Use large paragraph spacing to push content down
    for _ in range(8):
        _add_para(doc, "", size=Pt(11))

    # Firm contact details at bottom
    _add_para(doc, "{{ firm_name }}", size=Pt(9),
              alignment=WD_ALIGN_PARAGRAPH.CENTER)
    _add_para(doc, "{{ firm_address_1 }}", size=Pt(9),
              alignment=WD_ALIGN_PARAGRAPH.CENTER)
    _add_para(doc, "{{ firm_address_2 }}", size=Pt(9),
              alignment=WD_ALIGN_PARAGRAPH.CENTER)
    _add_para(doc, "{{ firm_phone }}", size=Pt(9),
              alignment=WD_ALIGN_PARAGRAPH.CENTER)
    _add_para(doc, "{{ firm_email }}", size=Pt(9),
              alignment=WD_ALIGN_PARAGRAPH.CENTER)

    return doc


def _build_detailed_pl(entity_type):
    """Build Detailed P&L template."""
    doc = Document()
    _set_default_font(doc)
    _set_page_setup(doc)
    _add_repeating_header(doc, "Detailed Profit and Loss Statement", "{{ date_text }}")
    _add_footer(doc)

    # Income section
    _add_financial_table(doc, "Income", "income", "Total Income",
                         "{{ total_income_cy }}", "{{ total_income_py }}")

    doc.add_paragraph("")  # spacer

    # Expenses section
    _add_financial_table(doc, "Expenses", "expenses", "Total Expenses",
                         "{{ total_expenses_cy }}", "{{ total_expenses_py }}")

    doc.add_paragraph("")  # spacer

    # Net Profit — single row table with grand total borders (no duplicate heading)
    _add_total_row(doc, "Net Profit / (Loss)",
                   "{{ net_profit_cy }}", "{{ net_profit_py }}")

    return doc


def _build_balance_sheet(entity_type):
    """Build Balance Sheet template."""
    doc = Document()
    _set_default_font(doc)
    _set_page_setup(doc)
    _add_repeating_header(doc, "Detailed Balance Sheet", "As at {{ year_end_date }}")
    _add_footer(doc)

    # Current Assets
    _add_financial_table(doc, "Current Assets", "current_assets", "Total Current Assets",
                         "{{ total_current_assets_cy }}", "{{ total_current_assets_py }}")
    doc.add_paragraph("")

    # Non-Current Assets
    _add_financial_table(doc, "Non-Current Assets", "noncurrent_assets",
                         "Total Non-Current Assets",
                         "{{ total_noncurrent_assets_cy }}", "{{ total_noncurrent_assets_py }}")
    doc.add_paragraph("")

    # Total Assets
    _add_total_row(doc, "Total Assets", "{{ total_assets_cy }}", "{{ total_assets_py }}")
    doc.add_paragraph("")

    # Current Liabilities
    _add_financial_table(doc, "Current Liabilities", "current_liabilities",
                         "Total Current Liabilities",
                         "{{ total_current_liab_cy }}", "{{ total_current_liab_py }}")
    doc.add_paragraph("")

    # Non-Current Liabilities
    _add_financial_table(doc, "Non-Current Liabilities", "noncurrent_liabilities",
                         "Total Non-Current Liabilities",
                         "{{ total_noncurrent_liab_cy }}", "{{ total_noncurrent_liab_py }}")
    doc.add_paragraph("")

    # Total Liabilities
    _add_total_row(doc, "Total Liabilities", "{{ total_liabilities_cy }}", "{{ total_liabilities_py }}")
    doc.add_paragraph("")

    # Net Assets
    _add_total_row(doc, "Net Assets", "{{ net_assets_cy }}", "{{ net_assets_py }}", size=Pt(12))

    # Equity — flows directly after Net Assets without forced page break
    _add_financial_table(doc, "Equity", "equity", "Total Equity",
                         "{{ total_equity_cy }}", "{{ total_equity_py }}")

    return doc


def _build_summary_pl(entity_type):
    """Build Summary P&L template (companies only)."""
    doc = Document()
    _set_default_font(doc)
    _set_page_setup(doc)
    _add_repeating_header(doc, "Summary Profit and Loss Statement", "{{ date_text }}")
    _add_footer(doc)

    # Summary table
    table = doc.add_table(rows=6, cols=3)
    _set_table_full_width(table)
    table.autofit = False
    table.columns[0].width = Cm(10)
    table.columns[1].width = Cm(3)
    table.columns[2].width = Cm(3)

    rows_data = [
        ("", "{{ year }}\n$", "{{ prior_year }}\n$"),
        ("Total Income", "{{ total_income_cy }}", "{{ total_income_py }}"),
        ("Total Expenses", "{{ total_expenses_cy }}", "{{ total_expenses_py }}"),
        ("Net Profit / (Loss) Before Tax", "{{ net_profit_cy }}", "{{ net_profit_py }}"),
        ("Income Tax Expense", "-", "-"),
        ("Net Profit / (Loss) After Tax", "{{ net_profit_cy }}", "{{ net_profit_py }}"),
    ]

    for r, (label, cy, py) in enumerate(rows_data):
        table.rows[r].cells[0].text = label
        table.rows[r].cells[1].text = cy
        table.rows[r].cells[2].text = py
        for i in range(3):
            for p in table.rows[r].cells[i].paragraphs:
                p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if i >= 1 else WD_ALIGN_PARAGRAPH.LEFT
                for run in p.runs:
                    run.font.name = FONT_NAME
                    run.font.size = FONT_SIZE
                    if r == 0 or r >= 3:
                        run.bold = True

    # Fix 1: Apply borders to Summary P&L rows
    # Row 3 = "Net Profit Before Tax" → subtotal
    _apply_subtotal_borders(table.rows[3])
    # Row 5 = "Net Profit After Tax" → grand total
    _apply_grand_total_borders(table.rows[5])

    return doc


def _build_notes(entity_type):
    """Build Notes template."""
    doc = Document()
    _set_default_font(doc)
    _set_page_setup(doc)
    _add_repeating_header(doc, "Notes to the Financial Statements", "{{ date_text }}")
    _add_footer(doc)


    _add_para(doc, "Note 1: Statement of Significant Accounting Policies",
              bold=True, keep_with_next=True)
    _add_para(doc, "The financial statements are special purpose financial statements "
              "prepared in order to satisfy the financial reporting requirements of the "
              "Corporations Act 2001 or relevant trust deed. The directors/trustees have "
              "determined that the entity is not a reporting entity.")
    doc.add_paragraph("")
    _add_para(doc, "The financial statements have been prepared on an accruals basis and "
              "are based on historical costs.")
    doc.add_paragraph("")
    _add_para(doc, "The following significant accounting policies have been adopted in "
              "the preparation and presentation of the financial statements:")
    doc.add_paragraph("")

    _add_para(doc, "a) Revenue Recognition", bold=True, keep_with_next=True)
    _add_para(doc, "Revenue is recognised when the entity satisfies a performance obligation "
              "by transferring a promised good or service to a customer.")
    doc.add_paragraph("")

    _add_para(doc, "b) Income Tax", bold=True, keep_with_next=True)
    _add_para(doc, "The income tax expense for the year comprises current income tax expense. "
              "Current income tax expense reflects the current year tax payable based on "
              "taxable income for the year.")
    doc.add_paragraph("")

    _add_para(doc, "c) Goods and Services Tax (GST)", bold=True, keep_with_next=True)
    _add_para(doc, "Revenues, expenses and assets are recognised net of the amount of GST. "
              "Receivables and payables are stated with the amount of GST included.")

    return doc


def _build_declaration(entity_type):
    """Build Declaration template."""
    doc = Document()
    _set_default_font(doc)
    _set_page_setup(doc)
    _add_repeating_header(doc, "{{ declaration_title }}", "{{ date_text }}")
    _add_page_number_footer(doc)


    if entity_type == "company":
        _add_para(doc,
                  "The directors of the company declare that the financial statements and "
                  "notes, as set out within this report, present fairly the company's financial "
                  "position as at {{ year_end_date }} and its performance for the year ended on "
                  "that date in accordance with the accounting policies described in Note 1 to "
                  "the financial statements.")
    elif entity_type == "trust":
        _add_para(doc,
                  "The trustee of the trust declares that the financial statements and notes, "
                  "as set out within this report, present fairly the trust's financial position "
                  "as at {{ year_end_date }} and its performance for the year ended on that date "
                  "in accordance with the accounting policies described in Note 1 to the "
                  "financial statements.")
    elif entity_type == "sole_trader":
        _add_para(doc,
                  "The proprietor declares that the financial statements and notes, as set out "
                  "within this report, present fairly the financial position as at "
                  "{{ year_end_date }} and the performance for the year ended on that date.")
    elif entity_type == "partnership":
        _add_para(doc,
                  "The partners declare that the financial statements and notes, as set out "
                  "within this report, present fairly the partnership's financial position as "
                  "at {{ year_end_date }} and its performance for the year ended on that date.")

    doc.add_paragraph("")
    _add_para(doc,
              "In the opinion of the {{ compilation_responsible_party }}, there are reasonable "
              "grounds to believe that the entity will be able to pay its debts as and when "
              "they become due and payable.")
    doc.add_paragraph("")

    _add_para(doc, "This declaration is made in accordance with a resolution of the "
              "{{ compilation_responsible_party }}.")
    doc.add_paragraph("")
    doc.add_paragraph("")

    # Signature block using Jinja2 for loop
    _add_para(doc, "{% for d in directors %}")
    _add_para(doc, "____________________________")
    _add_para(doc, "{{ d.name }}")
    _add_para(doc, "{{ d.title }}")
    _add_para(doc, "")
    _add_para(doc, "{% endfor %}")

    _add_para(doc, "Dated: {{ signing_date }}")

    return doc


def _build_compilation(entity_type):
    """Build Compilation Report (APES 315) template."""
    doc = Document()
    _set_default_font(doc)
    _set_page_setup(doc)
    _add_repeating_header(doc, "Compilation Report", "{{ date_text }}")
    _add_page_number_footer(doc)


    _add_para(doc, "To the {{ compilation_responsible_party }} of {{ entity_name }}")
    doc.add_paragraph("")

    _add_para(doc, "Scope", bold=True)
    _add_para(doc,
              "We have compiled the accompanying special purpose financial statements of "
              "{{ entity_name }}, which comprise the balance sheet as at {{ year_end_date }}, "
              "the profit and loss statement for the year then ended, and notes to the "
              "financial statements including a summary of significant accounting policies.")
    doc.add_paragraph("")

    _add_para(doc, "The Responsibility of the {{ compilation_responsible_party | capitalize }}", bold=True)
    _add_para(doc,
              "The {{ compilation_responsible_party }} of {{ entity_name }} are solely "
              "responsible for the information contained in the special purpose financial "
              "statements, and have determined that the accounting policies used are "
              "consistent and are appropriate to satisfy the requirements of the "
              "{{ compilation_responsible_party }}.")
    doc.add_paragraph("")

    _add_para(doc, "Our Responsibility", bold=True)
    _add_para(doc,
              "On the basis of information provided by the {{ compilation_responsible_party }}, "
              "we have compiled the accompanying special purpose financial statements in "
              "accordance with the applicable financial reporting framework and APES 315 "
              "Compilation of Financial Information.")
    doc.add_paragraph("")

    _add_para(doc,
              "We have applied our expertise in accounting and financial reporting to compile "
              "these financial statements in accordance with the applicable financial reporting "
              "framework. We have complied with the relevant ethical requirements of APES 110 "
              "Code of Ethics for Professional Accountants (including Independence Standards).")
    doc.add_paragraph("")

    _add_para(doc, "Assurance Disclaimer", bold=True, keep_with_next=True)
    _add_para(doc,
              "Since a compilation engagement is not an assurance engagement, we are not "
              "required to verify the reliability, accuracy or completeness of the information "
              "provided to us by management and the {{ compilation_responsible_party }} to "
              "compile these financial statements. Accordingly, we do not express an audit "
              "opinion or a review conclusion on these financial statements.")
    doc.add_paragraph("")

    _add_para(doc, "The special purpose financial statements were compiled exclusively for "
              "the benefit of the {{ compilation_responsible_party }} who are responsible for "
              "the reliability, accuracy and completeness of the information compiled. We do "
              "not accept responsibility for the contents of the special purpose financial "
              "statements.", keep_with_next=True)
    doc.add_paragraph("")

    _add_para(doc, "{{ firm_name }}", bold=True, keep_with_next=True)
    _add_para(doc, "{{ firm_address_1 }}", keep_with_next=True)
    _add_para(doc, "{{ firm_address_2 }}", keep_with_next=True)
    doc.add_paragraph("")
    _add_para(doc, "Dated: {{ signing_date }}")

    return doc


def _build_distribution(entity_type):
    """Build Distribution Summary template (trusts only)."""
    doc = Document()
    _set_default_font(doc)
    _set_page_setup(doc)
    _add_repeating_header(doc, "Beneficiaries Distribution Summary", "{{ date_text }}")
    _add_footer(doc)


    _add_para(doc, "Net Income Available for Distribution: {{ total_distribution }}", bold=True)
    doc.add_paragraph("")

    # Distribution table
    table = doc.add_table(rows=1, cols=3)
    _set_table_full_width(table)
    table.autofit = False
    table.columns[0].width = Cm(8)
    table.columns[1].width = Cm(4)
    table.columns[2].width = Cm(4)

    hdr = table.rows[0]
    hdr.cells[0].text = "Beneficiary"
    hdr.cells[1].text = "Percentage"
    hdr.cells[2].text = "Amount\n$"
    for i in range(3):
        for p in hdr.cells[i].paragraphs:
            for run in p.runs:
                run.font.name = FONT_NAME
                run.font.size = FONT_SIZE
                run.bold = True
            if i >= 1:
                p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    # Jinja2 loop rows
    row = table.add_row()
    row.cells[0].text = "{% for b in beneficiaries %}\n{{ b.beneficiary_name }}"
    row.cells[1].text = "{{ b.percentage }}%"
    row.cells[2].text = "{{ b.amount }}\n{% endfor %}"

    # Total row
    total_row = table.add_row()
    total_row.cells[0].text = "Total"
    total_row.cells[1].text = "100%"
    total_row.cells[2].text = "{{ total_distribution }}"
    for i in range(3):
        for p in total_row.cells[i].paragraphs:
            for run in p.runs:
                run.font.name = FONT_NAME
                run.font.size = FONT_SIZE
                run.bold = True

    return doc


# Builder dispatch
BUILDERS = {
    "COVER": _build_cover,
    "DETAILED_PL": _build_detailed_pl,
    "BALANCE_SHEET": _build_balance_sheet,
    "SUMMARY_PL": _build_summary_pl,
    "NOTES": _build_notes,
    "DECLARATION": _build_declaration,
    "COMPILATION": _build_compilation,
    "DISTRIBUTION": _build_distribution,
}


class Command(BaseCommand):
    help = "Generate default .docx financial statement templates and register them in the database"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite existing templates",
        )

    def handle(self, *args, **options):
        force = options["force"]
        output_dir = os.path.join(settings.MEDIA_ROOT, "fs_templates", "defaults")
        os.makedirs(output_dir, exist_ok=True)

        created = 0
        skipped = 0

        for entity_type in ENTITY_TYPES:
            applicable = ENTITY_DOC_TYPES.get(entity_type, [])
            for doc_type, doc_label in DOC_TYPES:
                if doc_type not in applicable:
                    continue

                # Check if active template already exists
                existing = FinancialStatementTemplate.objects.filter(
                    document_type=doc_type,
                    entity_type=entity_type,
                    is_active=True,
                ).first()

                if existing and not force:
                    self.stdout.write(
                        f"  SKIP {doc_type}/{entity_type} — already exists"
                    )
                    skipped += 1
                    continue

                # Build the template document
                builder = BUILDERS.get(doc_type)
                if not builder:
                    self.stdout.write(
                        self.style.WARNING(f"  No builder for {doc_type}")
                    )
                    continue

                doc = builder(entity_type)

                # Save to file
                filename = f"{doc_type}_{entity_type}.docx"
                filepath = os.path.join(output_dir, filename)
                doc.save(filepath)

                # Relative path from MEDIA_ROOT to the file on disk
                relative_path = f"fs_templates/defaults/{filename}"

                if existing and force:
                    # Update existing
                    existing.template_file = relative_path
                    existing.name = f"{doc_label} — {entity_type.replace('_', ' ').title()}"
                    existing.version = "1.0"
                    existing.save()
                    action = "UPDATED"
                else:
                    # Create new
                    tmpl = FinancialStatementTemplate(
                        name=f"{doc_label} — {entity_type.replace('_', ' ').title()}",
                        document_type=doc_type,
                        entity_type=entity_type,
                        version="1.0",
                        is_active=True,
                        template_file=relative_path,
                    )
                    tmpl.save()
                    action = "CREATED"

                self.stdout.write(
                    self.style.SUCCESS(f"  {action} {doc_type}/{entity_type}: {filename}")
                )
                created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone: {created} templates created/updated, {skipped} skipped."
            )
        )
