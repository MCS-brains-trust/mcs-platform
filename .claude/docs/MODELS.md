# StatementHub — Models Reference

## Core Models

### Entity
Represents a client entity (company, trust, partnership, sole trader, individual).
Key fields: `name`, `entity_type` (COMPANY/TRUST/PARTNERSHIP/SOLE_TRADER/INDIVIDUAL), `abn`, `industry_code`, `assigned_staff` (ForeignKey to User).
Entity type drives: document template selection, Eva compliance rules, financial statement structure.

### FinancialYear
Represents one financial year for one entity.
Key fields: `entity` (FK), `year` (int e.g. 2025), `status` (see WORKFLOWS.md), `locked` (bool).
Status choices: `draft`, `in_review`, `finalised`, `reopened`
One FinancialYear per entity per year. Never create duplicates.

### TrialBalanceLine
Stores individual account balances imported from Xero or entered manually.
Key fields: `financial_year` (FK), `account_name`, `account_code`, `account_type`, `net_amount` (current year Decimal), `prior_amount` (prior year Decimal), `source` (XERO/MANUAL/JOURNAL).
**Aggregation rule:** When rendering documents, always group by `account_name.strip().lower()` and sum amounts. Display name = most frequent original casing. Never render raw unaggregated lines.

### Journal / JournalLine
Adjusting journals entered within StatementHub.
All journal postings must be wrapped in `transaction.atomic()`.
Double-entry integrity: sum of all JournalLine amounts for a Journal must equal zero.
`verify_journal_tb_integrity --repair` runs in deploy pipeline.

### EvaFinding
Stores a compliance finding raised by Eva for a FinancialYear.
Key fields: `financial_year` (FK), `finding_key` (SHA-256 deterministic fingerprint), `category`, `severity` (CRITICAL/WARNING/ADVISORY), `status` (OPEN/RESOLVED/SUPPRESSED), `title`, `detail`, `rule_code`.
Findings are persistent — same finding_key = same finding, not a duplicate.

### EvaFindingSuppression
Stores a user decision to suppress a specific finding.
Key fields: `finding_key`, `entity` (FK), `suppressed_by` (FK User), `reason`.

### FinancialStatementTemplate
Stores uploaded .docx templates for document generation via docxtpl.
Key fields: `document_type` (COVER/DETAILED_PL/BALANCE_SHEET/SUMMARY_PL/NOTES/DECLARATION/COMPILATION/DISTRIBUTION), `entity_type` (COMPANY/TRUST/PARTNERSHIP/SOLE_TRADER), `template_file` (FileField → fs_templates/), `version`, `is_active`.
unique_together: `(document_type, entity_type, is_active)` — only one active template per type/entity combination.

### BASPeriodCommentary
Database-backed BAS commentary (not in-memory).
Key fields: `entity` (FK), `period`, `commentary_text`.

### WorkpaperTemplate
Stores uploaded .docx workpaper templates.
Key fields: `name`, `template_file`, `category`, `is_active`.
19 templates ready for upload — model and admin interface status: check current codebase.

### LegalDocumentTemplate
Stores docxtpl templates for legal documents (Div 7A agreements, trust deeds etc.).
Key fields: `document_type`, `template_file`, `version`, `is_active`.

---

## Field Conventions
- All monetary amounts: `DecimalField(max_digits=15, decimal_places=2)`
- All dates: `DateField` (not DateTime) unless audit trail required
- Soft deletes preferred over hard deletes for client-facing data
- `created_at` / `updated_at` auto timestamps on all major models

## Migration Rules
- Always `python3 manage.py makemigrations` — never write migration files manually
- After makemigrations locally, always push to GitHub before deploying
- Server-generated merge migrations must be pulled back to local immediately
- Deploy sequence always includes `python3 manage.py migrate` after git pull
