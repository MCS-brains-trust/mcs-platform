# StatementHub — Document Generation Reference

## Architecture (Current — docxtpl pipeline)

Financial statement documents are generated using **docxtpl** (Jinja2 templating inside Word .docx files).
The old python-docx programmatic approach in `core/docgen.py` has been replaced.

Pipeline:
1. `core/fs_template_service.py` — main service (8 functions, see below)
2. `FinancialStatementTemplate` model — stores active .docx templates per document_type + entity_type
3. LibreOffice headless — converts .docx to PDF on the server
4. `core/package_service.py` — assembles merged PDF client package

## fs_template_service.py Functions

| Function | Purpose |
|----------|---------|
| `format_amount(value, show_negative_brackets=True)` | Formats Decimal to financial string. Zero/None = "-". Negative = "(1,234)". No $ sign in cells. |
| `aggregate_tb_lines(queryset)` | Groups TrialBalanceLine by `account_name.strip().lower()`, sums cy/py amounts, returns list of dicts. Logs raw vs aggregated count. |
| `build_company_context(financial_year, include_watermark=True)` | Builds full Jinja2 context dict for company entity |
| `build_trust_context(financial_year, include_watermark=True)` | Builds context including beneficiary distribution data |
| `build_sole_trader_context(financial_year, include_watermark=True)` | Builds context including proprietors funds structure |
| `render_template(template_db_record, context)` | Loads .docx via DocxTemplate, renders Jinja2, returns BytesIO |
| `generate_financial_statements(financial_year_id, include_watermark=True)` | Orchestrates all templates, returns dict of document_type → BytesIO |
| `assemble_pdf_package(financial_year_id)` | Generates all docs with include_watermark=False, converts to PDF, merges, returns bytes |

## Watermark Rule
- `include_watermark=True` → "DRAFT" appears in header (red, 16pt)
- `include_watermark=False` → watermark cell is empty string
- **Client package assembly always calls with `include_watermark=False`** regardless of financial year status
- The Assemble Client Package button is only available after Eva review is complete

## Document Types and Order
| Order | document_type | Description |
|-------|--------------|-------------|
| 1 | COVER | Cover page + contents |
| 2 | DETAILED_PL | Detailed Profit and Loss Statement |
| 3 | BALANCE_SHEET | Detailed Balance Sheet |
| 4 | SUMMARY_PL | Summary P&L (companies only) |
| 5 | NOTES | Notes to Financial Statements |
| 6 | DECLARATION | Directors/Trustee/Proprietor Declaration |
| 7 | COMPILATION | Compilation Report (APES 315) |
| 8 | DISTRIBUTION | Beneficiaries Distribution Summary (trusts only) |

## Entity-Type Variations
| Entity Type | Declaration Title | Compilation Responsible Party | Balance Sheet Structure |
|-------------|------------------|-------------------------------|------------------------|
| COMPANY | Directors' Declaration | directors | Standard assets/liabilities/equity |
| TRUST | Trustee's Declaration | director of the trustee company | Standard + Distribution Summary |
| SOLE_TRADER | Proprietor Declaration | owner | Proprietors' Funds at top |
| PARTNERSHIP | Partners' Declaration | partners | Partners' Capital section |

## Template Layout Standard
- **Page:** A4 portrait, margins top 2cm, bottom 2cm, left 2.5cm, right 2cm
- **Font:** Calibri 10pt throughout
- **Tables:** 4-column layout — col1 8.5cm (name), col2 1.5cm (Note), col3 3cm (CY), col4 3cm (PY)
- **No tab stops** — all column alignment via Word tables only
- **Header:** entity name left | DRAFT watermark right (per page, except cover)
- **Footer:** unaudited statement 8pt italic (except cover, declaration, compilation)
- **Amounts:** right-aligned, comma separated, no $ in cells, negative in brackets
- **Totals:** bold, top border. Section totals: double top border. Net assets: double top and bottom border.

## Template Files Location
Default templates: `MEDIA_ROOT/fs_templates/defaults/`
Uploaded custom templates: `MEDIA_ROOT/fs_templates/`
Admin upload interface: Django Admin → Financial Statement Templates

## LibreOffice PDF Conversion
```python
subprocess.run(
    ['libreoffice', '--headless', '--convert-to', 'pdf', '--outdir', tmpdir, docx_path],
    capture_output=True, timeout=60
)
```
LibreOffice is installed at `/usr/bin/libreoffice` on the production server.
If conversion fails, log error and skip that document — do not raise exception.

## Aggregation Rule (Critical)
Raw Xero trial balance lines often have the same category split across multiple sub-accounts with minor name variations (e.g. "Computer expenses" vs "Computer Expenses").
**Always aggregate before rendering.** Grouping key: `account_name.strip().lower()`. Display name: most frequent original casing within group.
This is enforced in `aggregate_tb_lines()` — never iterate raw TrialBalanceLine querysets directly in document rendering.

## Old docgen.py
`core/docgen.py` is the deprecated python-docx programmatic approach. It has been replaced by `fs_template_service.py`. Do not add new features to docgen.py. It may be removed in a future cleanup.
