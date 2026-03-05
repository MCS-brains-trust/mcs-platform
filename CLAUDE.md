# StatementHub — Comprehensive Technical Specification

**Platform:** StatementHub  
**Firm:** MC & S Accountants  
**Production URL:** https://statementhub.com.au  
**Repository:** `faceless-truth/mcs-platform` (private)  
**Server Path:** `/opt/statementhub`  
**Prepared for:** Claude Code full codebase review  
**Spec Date:** March 2026  

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Technology Stack & Dependencies](#2-technology-stack--dependencies)
3. [Application Structure](#3-application-structure)
4. [Data Models — Complete Schema](#4-data-models--complete-schema)
5. [User Roles & Permissions](#5-user-roles--permissions)
6. [Financial Year Workflow & Lifecycle](#6-financial-year-workflow--lifecycle)
7. [Data Import Methods — All Pathways](#7-data-import-methods--all-pathways)
8. [Eva AI — Knowledge, Interactions & Processes](#8-eva-ai--knowledge-interactions--processes)
9. [Risk Engine — All Rules & Detection Modules](#9-risk-engine--all-rules--detection-modules)
10. [Document Generation — All Document Types](#10-document-generation--all-document-types)
11. [BAS / GST Module](#11-bas--gst-module)
12. [Bank Statement Review Module](#12-bank-statement-review-module)
13. [Tax Planning Module](#13-tax-planning-module)
14. [Trust Distribution Workspace](#14-trust-distribution-workspace)
15. [Division 7A Module](#15-division-7a-module)
16. [Office Admin Module](#16-office-admin-module)
17. [Third-Party Integrations](#17-third-party-integrations)
18. [Authentication & Security](#18-authentication--security)
19. [Background Tasks (Celery)](#19-background-tasks-celery)
20. [URL & Feature Inventory](#20-url--feature-inventory)
21. [Key Design Patterns & Gotchas](#21-key-design-patterns--gotchas)
22. [Management Commands](#22-management-commands)

---

## 1. Architecture Overview

StatementHub is a **monolithic Django 5.x application** deployed on a single VPS (`statementhub` server) behind Gunicorn. It is the primary practice management and financial statement platform for MC & S Accountants, designed to replace the firm's legacy on-premise `Access Ledger` software.

The platform follows a **server-rendered HTML** architecture using Django templates with **HTMX** for partial page updates and **Bootstrap 5** for styling. There is no separate frontend SPA — all logic is server-side.

### Architectural Principles

**Immutable Trial Balance:** Original trial balance imports are never modified. All adjustments create new `TrialBalanceLine` records with `is_adjustment=True`. The application layer calculates and displays netted balances. This is the single most important design invariant in the system.

**Account Mapping Hierarchy:** When resolving an account to a financial statement line, the system uses a three-tier lookup: `ClientAccountMapping` (entity-specific override) → `TrialBalanceLine.mapped_line_item` → `ChartOfAccount` (master CoA). This allows per-client customisation without polluting the master chart.

**Three-Tier AI Architecture:** The AI layer uses three LLM tiers — `haiku` (fast/cheap, classification), `sonnet` (balanced, analysis), `opus` (complex, reports). The system supports both Anthropic (Claude) and OpenAI-compatible APIs, switchable via the `USE_ANTHROPIC` environment variable.

**Celery + Redis for Async:** All long-running AI operations (Eva reviews, knowledge sync, document generation, package assembly) are dispatched as Celery tasks with Redis as the broker and result backend.

---

## 2. Technology Stack & Dependencies

| Layer | Technology |
| :--- | :--- |
| **Backend Framework** | Django 5.2.x |
| **WSGI Server** | Gunicorn 25.x |
| **Database** | PostgreSQL (production), SQLite (development) |
| **Task Queue** | Celery 5.3+ with Redis broker |
| **Cache / Broker** | Redis |
| **Frontend** | Bootstrap 5, HTMX 1.27, vanilla JavaScript (inline) |
| **Document Generation** | python-docx, docxtpl, WeasyPrint, LibreOffice (PDF conversion) |
| **PDF Parsing** | pdfplumber (bank statements), PyPDF2 |
| **AI/LLM** | Anthropic (Claude claude-haiku-4-5 / claude-sonnet-4-6 / claude-opus-4-6) or OpenAI-compatible (gpt-4.1-nano / gpt-4.1-mini) |
| **OCR** | AWS Textract (governing documents) |
| **Embeddings** | OpenAI text-embedding-ada-002 (Knowledge Brain) |
| **Knowledge Source** | Microsoft SharePoint (via Microsoft Graph API) |
| **E-Signing** | FuseSign API |
| **Encryption** | `cryptography` library (TFN, TOTP secrets) |
| **Spreadsheets** | openpyxl, pandas |
| **Static Files** | WhiteNoise |
| **Security** | django-csp, django-ratelimit |
| **2FA** | pyotp (TOTP), qrcode |

### Key Python Packages

```
Django==5.2.11
celery>=5.3.0
redis>=5.0.0
anthropic>=0.39.0
openai>=1.0.0
pdfplumber>=0.10.0
python-docx==1.2.0
docxtpl==0.20.2
weasyprint==68.1
openpyxl==3.1.5
pandas>=2.0.0
python-pptx>=0.6.21
extract-msg>=0.48.0
boto3>=1.34.0
django-celery-beat>=2.5.0
cryptography>=44.0.0
pyotp==2.9.0
```

---

## 3. Application Structure

The project lives at `/opt/statementhub` and is organised into four Django apps plus a `config` package.

```
statementhub/
├── config/               # Django project config (settings, urls, wsgi, middleware)
│   ├── settings.py
│   ├── urls.py
│   ├── middleware.py     # Require2FAMiddleware
│   └── context_processors.py
├── accounts/             # User management, roles, invitations, 2FA
├── core/                 # Main accounting app (all core logic)
│   ├── models.py         # 4,873 lines — all core models
│   ├── models_office_admin.py  # Office admin models
│   ├── views.py          # 11,098 lines — main views
│   ├── views_audit.py    # Chart of accounts, risk engine, audit library
│   ├── views_bas.py      # BAS/GST activity statement
│   ├── views_bas_commentary.py  # AI-generated BAS commentary
│   ├── views_bulk_operations.py # Bulk package generation
│   ├── views_client_summary.py  # Eva client summary
│   ├── views_compliance_docs.py # Dividends, solvency, directors' declarations
│   ├── views_div7a.py    # Division 7A dashboard
│   ├── views_eva.py      # Eva chat, review, knowledge brain admin
│   ├── views_governing_docs.py  # Trust deed / governing document upload
│   ├── views_legal_docs.py      # Legal document wizard & generation
│   ├── views_office_admin.py    # Office admin dashboard
│   ├── views_package_assembly.py # Client package assembly
│   ├── views_partnership_docs.py # Partner statements, engagement letters
│   ├── views_tax_planning.py    # Tax planning worksheet
│   ├── views_templates.py       # Financial statement template management
│   ├── views_trust.py           # Trust distribution workspace API
│   ├── views_upgrades.py        # Comparatives, roll-forward, bulk import
│   ├── eva_engine.py     # Eva review engine (14 compliance checks)
│   ├── eva_service.py    # Eva core: knowledge brain, chat, context building
│   ├── eva_chat.py       # Eva chat dispatch and context payload
│   ├── eva_knowledge.py  # SharePoint sync, chunking, embedding
│   ├── eva_trust_planning.py    # Trust planning conversation mode
│   ├── eva_div7a.py      # Div 7A assessment engine (8 rules)
│   ├── eva_bas_commentary.py    # BAS commentary generation
│   ├── eva_client_summary.py    # Client summary generation
│   ├── eva_amber.py      # Amber indicator computation
│   ├── eva_proactive.py  # Proactive suggestion generation
│   ├── eva_summary.py    # Re-export shim
│   ├── ai_service.py     # LLM abstraction layer (Anthropic/OpenAI)
│   ├── risk_engine.py    # Risk engine orchestrator
│   ├── risk_modules/     # Dedicated detection modules
│   │   ├── registry.py
│   │   ├── base.py
│   │   ├── div7a.py
│   │   ├── going_concern.py
│   │   ├── section100a.py
│   │   ├── cluster_rp.py
│   │   ├── cluster_sgc.py
│   │   └── cluster_tpar.py
│   ├── docgen.py         # Financial statements document generation
│   ├── mgmt_accounts.py  # Management accounts generation
│   ├── distmin_gen.py    # Distribution minutes generation
│   ├── taxplan_docgen.py # Tax planning summary document
│   ├── template_docgen.py # Template-based document rendering
│   ├── template_renderer.py
│   ├── template_resolvers.py
│   ├── legal_doc_service.py  # Legal document generation service
│   ├── legal_doc_contexts.py # Context builders for legal docs
│   ├── package_service.py    # Client package assembly service
│   ├── access_ledger_import.py  # Access Ledger import service
│   ├── bas_utils.py      # BAS calculation utilities
│   ├── tax_engine.py     # Tax calculation engine
│   ├── mapping_engine.py # Account mapping engine
│   ├── ocr_service.py    # OCR service wrapper
│   ├── tasks.py          # Celery task definitions
│   ├── signals.py        # Django signals
│   ├── industry_codes.py # ATO Business Industry Codes (NAT 1827)
│   └── management/commands/
│       ├── seed_risk_rules.py
│       ├── seed_account_mappings.py
│       ├── import_access_ledger.py
│       ├── import_chart_of_accounts.py
│       ├── sync_knowledge_brain.py
│       ├── map_accounts.py
│       ├── remap_trial_balances.py
│       └── scrape_ato_updates.py
├── review/               # Bank statement processing workflow
│   ├── models.py
│   ├── views.py          # 2,636 lines
│   ├── pdf_parsers.py    # Bank-specific PDF parsers
│   └── management/commands/clear_test_data.py
└── integrations/         # Third-party accounting software connections
    ├── models.py
    ├── views.py          # 1,699 lines
    ├── xpm_sync.py       # Xero Practice Manager sync
    └── urls.py
```

---

## 4. Data Models — Complete Schema

### 4.1 `accounts` App

#### `User` (extends `AbstractUser`)
Custom user model with role-based access control and TOTP 2FA.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `role` | CharField | Choices: `admin`, `senior_accountant`, `accountant`, `office_admin`, `read_only` |
| `phone` | CharField | |
| `is_active` | BooleanField | |
| `totp_secret` | EncryptedCharField | Encrypted at rest |
| `totp_confirmed` | BooleanField | Whether TOTP setup is confirmed |

#### `Invitation`
Manages user onboarding via email invitation tokens.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `email` | EmailField | |
| `role` | CharField | Role to assign on signup |
| `token` | CharField | Unique invitation token |
| `invited_by` | FK → User | |
| `status` | CharField | `pending`, `accepted`, `expired`, `revoked` |
| `expires_at` | DateTimeField | |

---

### 4.2 `core` App — Entity & Client Models

#### `Client`
Top-level grouping for related entities (e.g. a family group or business group).

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `name` | CharField | Client/group name |
| `created_at` | DateTimeField | |

#### `Entity`
The primary accounting entity. One entity = one ABN/ACN.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `client` | FK → Client | |
| `entity_name` | CharField | |
| `entity_type` | CharField | `company`, `trust`, `partnership`, `sole_trader`, `smsf` |
| `abn` | CharField | |
| `acn` | CharField | Companies only |
| `tfn` | EncryptedCharField | Encrypted at rest |
| `registration_date` | DateField | |
| `financial_year_end` | CharField | e.g. "30 June" |
| `reporting_framework` | CharField | `GPFR_tier1`, `GPFR_tier2`, `SPFR` |
| `company_size` | CharField | `small_proprietary`, `large_proprietary`, `public` |
| `industry` | CharField | ATO Business Industry Code (NAT 1827) |
| `is_gst_registered` | BooleanField | |
| `gst_registration_date` | DateField | |
| `bas_frequency` | CharField | `quarterly`, `monthly` |
| `address_line_1/2` | CharField | |
| `suburb`, `state`, `postcode` | CharField | |
| `trustee_name` | CharField | Trusts/SMSFs |
| `trustee_acn` | CharField | Trustee company ACN |
| `is_large_proprietary` | BooleanField | |
| `assigned_to` | FK → User | Primary accountant |
| `created_at` | DateTimeField | |

#### `EntityOfficer`
Directors, trustees, partners, beneficiaries, shareholders of an entity.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `entity` | FK → Entity | |
| `name` | CharField | |
| `role` | CharField | e.g. Director, Trustee, Beneficiary |
| `email` | EmailField | |
| `date_of_birth` | DateField | |
| `tfn` | EncryptedCharField | |
| `abn` | CharField | |
| `address` | TextField | |
| `is_primary` | BooleanField | |

#### `ClientAssociate`
Related individuals/entities associated with a client (e.g. spouse, accountant, solicitor).

#### `EntityRelationship`
Links between entities (e.g. trustee company → trust, holding company → subsidiary).

#### `AccountingSoftware`
Records which accounting software an entity uses (Xero, MYOB, QuickBooks, etc.).

#### `MeetingNote`
Meeting notes and follow-up items for an entity.

---

### 4.3 `core` App — Financial Year & Trial Balance Models

#### `FinancialYear`
The central record for a reporting period. All accounting work is anchored to a FinancialYear.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `entity` | FK → Entity | |
| `year_label` | CharField | e.g. "FY2025" |
| `period_type` | CharField | `annual`, `half_year`, `quarterly`, `monthly`, `interim` |
| `start_date` | DateField | |
| `end_date` | DateField | |
| `status` | CharField | See lifecycle below |
| `locked_by` | FK → User | |
| `locked_at` | DateTimeField | |
| `reporting_framework` | CharField | Inherited from entity, overridable |
| `has_going_concern` | BooleanField | |
| `going_concern_notes` | TextField | |
| `prior_year` | FK → FinancialYear (self) | |

**Status Lifecycle:**
`draft` → `in_review` → `finished` → `prepared` → `pending_eva` → `eva_cleared` / `eva_error` → `locked`
(Legacy: `finalised` maps to `locked`)

#### `TrialBalanceLine`
Individual account lines in the trial balance. Both original imports and adjustments are stored here.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `financial_year` | FK → FinancialYear | |
| `account_code` | CharField | |
| `account_name` | CharField | |
| `debit` | DecimalField | |
| `credit` | DecimalField | |
| `prior_debit` | DecimalField | Prior year comparative |
| `prior_credit` | DecimalField | |
| `opening_balance` | DecimalField | |
| `prior_closing_balance` | DecimalField | |
| `is_adjustment` | BooleanField | True for journal-created lines |
| `mapped_line_item` | FK → AccountMapping | |
| `source` | CharField | `import`, `manual_journal`, `bank_review`, `bulk_upload` |
| `bulk_upload` | FK → BulkJournalUpload | |
| `description` | TextField | |
| `tax_code` | CharField | GST tax code |
| `is_contra` | BooleanField | Bank contra entries |
| `comparative_locked` | BooleanField | |
| `comparative_override` | DecimalField | Manual comparative override |

#### `AccountMapping`
Master chart of accounts — maps account codes to financial statement sections and line items.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `account_code` | CharField | Standard code |
| `account_name` | CharField | |
| `statement_section` | CharField | e.g. `revenue`, `expenses`, `assets`, `liabilities`, `equity` |
| `line_item_label` | CharField | Display label in financial statements |
| `sort_order` | IntegerField | |
| `entity_type` | CharField | Which entity types this applies to |

#### `ChartOfAccount`
Master chart of accounts (firm-wide standard).

#### `EntityChartOfAccount`
Entity-specific chart of accounts overrides.

#### `ClientAccountMapping`
Entity-specific account code → AccountMapping overrides. Highest priority in the three-tier lookup.

#### `BankAccountMapping`
Maps bank accounts (BSB + account number) to trial balance account codes for bank statement review.

---

### 4.4 `core` App — Journals & Adjustments

#### `AdjustingJournal`
A journal entry (collection of debit/credit lines).

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `financial_year` | FK → FinancialYear | |
| `description` | TextField | |
| `journal_date` | DateField | |
| `created_by` | FK → User | |
| `is_posted` | BooleanField | |
| `posted_at` | DateTimeField | |
| `source` | CharField | `manual`, `bulk_upload`, `depreciation`, `stock`, `bank_review` |
| `bulk_upload` | FK → BulkJournalUpload | |

#### `JournalLine`
Individual debit/credit line within a journal.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `journal` | FK → AdjustingJournal | |
| `account_code` | CharField | |
| `account_name` | CharField | |
| `debit` | DecimalField | |
| `credit` | DecimalField | |
| `description` | CharField | |

#### `BulkJournalUpload`
Tracks bulk journal uploads (Excel template).

---

### 4.5 `core` App — Document Generation Models

#### `FinancialStatementTemplate`
Defines the structure of financial statements (which sections/line items to include).

#### `DocumentTemplate`
Template configuration for financial statement documents (header, footer, notes structure).

#### `GeneratedDocument`
Record of every generated financial statement document.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `financial_year` | FK → FinancialYear | |
| `file` | FileField | Stored .docx or .pdf |
| `file_format` | CharField | `docx`, `pdf` |
| `generated_by` | FK → User | |
| `is_final` | BooleanField | |
| `generated_at` | DateTimeField | |

#### `LegalDocumentTemplate`
Word (.docx) templates for legal and compliance documents. Supports 35 document types (see Section 10).

#### `LegalDocument`
A generated instance of a legal/compliance document.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `entity` | FK → Entity | |
| `financial_year` | FK → FinancialYear | Optional |
| `template` | FK → LegalDocumentTemplate | |
| `document_type` | CharField | One of 35 types |
| `title` | CharField | |
| `version` | PositiveIntegerField | |
| `status` | CharField | `draft`, `generated`, `final`, `executed` |
| `fusesign_status` | CharField | `not_sent`, `sent`, `signed`, `declined` |
| `fusesign_envelope_id` | CharField | FuseSign envelope ID |
| `context_data` | JSONField | Structured context used for rendering |
| `parameters` | JSONField | Template variable values |
| `generated_file` | FileField | .docx |
| `pdf_file` | FileField | .pdf |

#### `GoverningDocument`
Trust deeds, company constitutions, and other governing documents uploaded for OCR extraction.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `entity` | FK → Entity | |
| `document_type` | CharField | `trust_deed`, `constitution`, `partnership_agreement` |
| `file` | FileField | |
| `extracted_text` | TextField | OCR/native text extraction |
| `extraction_method` | CharField | `native`, `textract` |
| `textract_job_id` | CharField | AWS Textract async job ID |
| `ocr_confidence` | FloatField | |
| `is_archived` | BooleanField | |

---

### 4.6 `core` App — Risk & Audit Models

#### `RiskRule`
Seeded risk rules (from `seed_risk_rules.py`). 80+ rules across 15 categories.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `rule_id` | CharField | e.g. "D7A-01", "GEN-05" |
| `category` | CharField | |
| `title` | CharField | |
| `description` | TextField | Template string with `{entity_name}`, `{total}` etc. |
| `severity` | CharField | `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`, `ADVISORY` |
| `tier` | IntegerField | 1 (variance) or 2 (rule-based) |
| `applicable_entities` | JSONField | List of entity types |
| `trigger_config` | JSONField | Trigger type and parameters |
| `recommended_action` | TextField | |
| `legislation_ref` | CharField | |

#### `RiskFlag`
An instance of a risk rule triggered for a specific financial year.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `financial_year` | FK → FinancialYear | |
| `run_id` | UUIDField | Groups flags from same analysis run |
| `rule_id` | CharField | |
| `tier` | IntegerField | |
| `severity` | CharField | |
| `title` | CharField | |
| `description` | TextField | Rendered description |
| `recommended_action` | TextField | |
| `legislation_ref` | CharField | |
| `status` | CharField | `open`, `reviewed`, `resolved`, `auto_resolved` |
| `ai_analysis` | TextField | LLM analysis narrative |
| `ai_priority_score` | IntegerField | 1–10 |
| `ai_priority_rationale` | TextField | |
| `flag_hash` | CharField | Deduplication hash |
| `resolved_by` | FK → User | |
| `resolved_at` | DateTimeField | |
| `resolution_notes` | TextField | |

#### `RiskReferenceData`
ATO benchmark data and industry reference figures used by the risk engine.

#### `AuditLog`
Immutable audit trail of all significant actions.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `user` | FK → User | |
| `action` | CharField | See action choices below |
| `description` | TextField | |
| `affected_object_type` | CharField | |
| `affected_object_id` | CharField | |
| `metadata` | JSONField | |
| `timestamp` | DateTimeField | |
| `ip_address` | GenericIPAddressField | |

**Action Choices:** `view`, `login`, `logout`, `import`, `adjustment`, `generate`, `status_change`, `mapping_change`, `user_change`, `template_change`, `ai_feedback`, `reopen`, `eva_chat`, `eva_review`, `eva_finding`, `eva_sync`

---

### 4.7 `core` App — Eva AI Models

#### `EvaReview`
A full Eva compliance review run for a financial year.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `financial_year` | FK → FinancialYear | |
| `status` | CharField | `pending`, `running`, `completed`, `failed` |
| `triggered_by` | FK → User | |
| `started_at` | DateTimeField | |
| `completed_at` | DateTimeField | |
| `checks_run` | JSONField | List of check IDs run |
| `error_message` | TextField | |

#### `EvaFinding`
An individual finding from an Eva review (one per compliance check per review).

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `review` | FK → EvaReview | |
| `check_name` | CharField | e.g. "Division 7A Loan Compliance" |
| `check_id` | CharField | e.g. "div7a" |
| `title` | CharField | |
| `severity` | CharField | `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`, `ADVISORY` |
| `narrative` | TextField | LLM-generated analysis |
| `recommendations` | JSONField | List of recommendation strings |
| `cross_references` | JSONField | Related check IDs |
| `status` | CharField | `open`, `resolved` |
| `resolved_by` | FK → User | |
| `resolved_at` | DateTimeField | |
| `resolution_notes` | TextField | |

#### `KnowledgeDocument`
A document in Eva's Knowledge Brain, synced from SharePoint.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `title` | CharField | |
| `category` | CharField | 20 categories (see below) |
| `sharepoint_path` | CharField | Full SharePoint path |
| `sharepoint_item_id` | CharField | SharePoint item ID |
| `sharepoint_modified_at` | DateTimeField | |
| `sync_status` | CharField | `pending`, `synced`, `error` |
| `synced_at` | DateTimeField | |
| `chunk_count` | IntegerField | |
| `file_type` | CharField | `docx`, `pdf`, `txt`, `xlsx`, `pptx`, `msg` |
| `file_size_bytes` | IntegerField | |
| `is_archived` | BooleanField | |

**Knowledge Document Categories:** `firm_procedures`, `firm_technical`, `firm_training`, `firm_precedents`, `ato_rulings`, `ato_statements`, `ato_alerts`, `ato_benchmarks`, `legislation`, `aasb_standards`, `cpa_materials`, `ca_anz_materials`, `treasury`, `apes_standards`, `tpb_guidance`, `case_law`, `industry_guides`, `client_precedents`, `other`

#### `KnowledgeChunk`
A text chunk from a KnowledgeDocument, with vector embedding for semantic search.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `document` | FK → KnowledgeDocument | |
| `chunk_index` | IntegerField | Position within document |
| `text` | TextField | Raw chunk text |
| `embedding` | JSONField | Vector embedding (list of floats) |
| `token_count` | IntegerField | |

#### `EvaConversation`
A chat conversation session between a user and Eva.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `financial_year` | FK → FinancialYear | |
| `user` | FK → User | |
| `started_at` | DateTimeField | |
| `last_message_at` | DateTimeField | |

#### `EvaMessage`
Individual messages within an Eva conversation.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `conversation` | FK → EvaConversation | |
| `role` | CharField | `user`, `assistant` |
| `content` | TextField | |
| `knowledge_chunks_used` | JSONField | RAG chunks retrieved |
| `model_tier` | CharField | Which LLM tier was used |
| `created_at` | DateTimeField | |

#### `EvaTrustPlanningSession`
Links a trust planning conversation to a financial year.

#### `EvaClientSummary`
AI-generated client summary (generated when a financial year is locked).

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `financial_year` | FK → FinancialYear | |
| `format_type` | CharField | `bullet`, `narrative` |
| `financial_highlights` | TextField | |
| `compliance_status` | TextField | |
| `tax_position` | TextField | |
| `recommendations` | TextField | |
| `year_on_year_comparison` | TextField | |
| `full_content` | TextField | |
| `version` | PositiveIntegerField | |
| `model_used` | CharField | |
| `generated_at` | DateTimeField | |

---

### 4.8 `core` App — BAS / GST Models

#### `BASPeriod`
Tracks the status and lodgement audit snapshot for each BAS period.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `financial_year` | FK → FinancialYear | |
| `period_type` | CharField | `quarterly`, `monthly` |
| `period_number` | PositiveSmallIntegerField | 1–4 (quarterly) or 1–12 (monthly) |
| `period_start` | DateField | |
| `period_end` | DateField | |
| `status` | CharField | `empty`, `partial`, `ready`, `lodged` |
| `lodged_by` | FK → User | |
| `lodged_at` | DateTimeField | |
| `unlodged_by` | FK → User | |
| `unlodged_at` | DateTimeField | |
| `snapshot_1a` | DecimalField | GST on Sales at time of lodgement |
| `snapshot_1b` | DecimalField | GST on Purchases at time of lodgement |

#### `BASPeriodCommentary`
AI-generated period commentary for a BAS period.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `financial_year` | FK → FinancialYear | |
| `bas_period` | FK → BASPeriod | Optional (null for custom date ranges) |
| `period_start` | DateField | |
| `period_end` | DateField | |
| `status` | CharField | `generating`, `draft`, `reviewed`, `sent`, `error` |
| `tone` | CharField | `professional`, `conversational`, `technical` |
| `commentary_json` | JSONField | Structured commentary sections |
| `full_text` | TextField | Rendered full commentary |
| `docx_file` | FileField | |
| `sent_at` | DateTimeField | |
| `version` | PositiveIntegerField | |

---

### 4.9 `core` App — Tax Planning Models

#### `TaxPlanningWorksheet`
Tax planning calculations for a financial year.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `financial_year` | FK → FinancialYear | |
| `estimated_taxable_income` | DecimalField | |
| `tax_payable` | DecimalField | |
| `medicare_levy` | DecimalField | |
| `franking_credits` | DecimalField | |
| `prior_year_tax_paid` | DecimalField | |
| `instalment_rate` | DecimalField | |
| `notes` | TextField | |
| `is_finalised` | BooleanField | |
| `finalised_by` | FK → User | |
| `finalised_at` | DateTimeField | |

#### `TaxPlanningBeneficiaryRow`
Per-beneficiary tax calculations within a trust tax planning worksheet.

#### `TaxPlanningScenario`
Named "what-if" scenarios within a tax planning worksheet.

---

### 4.10 `core` App — Trust Distribution Models

#### `TrustDistribution`
Records the trust distribution resolution for a financial year.

#### `BeneficiaryAllocation`
Individual beneficiary allocations within a trust distribution.

#### `TrustWorkspace`
Master workspace for the 6-stage trust distribution workflow.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `financial_year` | OneToOneField → FinancialYear | |
| `stage_1_status` through `stage_6_status` | CharField | `not_started`, `in_progress`, `completed` |
| `overall_100a_risk` | CharField | `green`, `amber`, `red` |
| `notes` | TextField | |

**6 Stages:**
1. Income Calculation
2. Beneficiary Profiling
3. Distribution Modelling
4. Section 100A Review
5. Trust Elections
6. Resolution Preparation

#### `BeneficiaryProfile`
Tax profile for a beneficiary within a trust distribution workspace.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `trust_workspace` | FK → TrustWorkspace | |
| `beneficiary` | FK → EntityOfficer | |
| `beneficiary_type` | CharField | `adult`, `minor`, `company`, `trust`, `smsf` |
| `other_income` | DecimalField | Other taxable income outside this trust |
| `marginal_rate` | DecimalField | e.g. 0.3250 for 32.5% |
| `bracket_remaining` | DecimalField | Remaining capacity in current tax bracket |

#### `DistributionScenario`
A named distribution scenario (up to 3 per workspace).

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `trust_workspace` | FK → TrustWorkspace | |
| `name` | CharField | e.g. "Scenario 1" |
| `allocations` | JSONField | `{beneficiary_id: {stream: amount, ...}, ...}` |
| `total_tax` | DecimalField | |
| `is_confirmed` | BooleanField | |

#### `Section100AAssessment`
Section 100A risk assessment for a trust workspace.

#### `TrustElectionRecord`
Records trust elections (Family Trust Election, Interposed Entity Election).

---

### 4.11 `core` App — Division 7A Models

#### `Div7AAssessment`
Consolidated Div 7A assessment per entity per financial year (produced by the Div 7A detection module).

#### `Div7ACompliance`
Tracks compliance status of each Div 7A loan arrangement.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `entity` | FK → Entity | |
| `borrower_name` | CharField | |
| `borrower_entity` | FK → Entity | If borrower is another StatementHub entity |
| `loan_amount` | DecimalField | Original loan amount |
| `loan_start_date` | DateField | |
| `loan_start_year` | IntegerField | FY loan commenced |
| `loan_term` | IntegerField | 7 (unsecured) or 25 (secured) |
| `is_secured` | BooleanField | |
| `agreement_document` | FK → LegalDocument | |
| `status` | CharField | `COMPLIANT`, `NON_COMPLIANT`, `EXPIRED`, `PENDING` |

---

### 4.12 `core` App — Going Concern Model

#### `GoingConcernAssessment`
Consolidated going concern assessment per entity per financial year.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `financial_year` | OneToOneField → FinancialYear | |
| `net_assets` | DecimalField | |
| `cash_position` | DecimalField | |
| `cy_revenue` | DecimalField | Current year revenue |
| `py_revenue` | DecimalField | Prior year revenue |
| `assessed_at` | DateTimeField | |

---

### 4.13 `core` App — Depreciation & Stock Models

#### `DepreciationAsset`
Fixed asset register entry.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `financial_year` | FK → FinancialYear | |
| `asset_name` | CharField | |
| `purchase_date` | DateField | |
| `cost` | DecimalField | |
| `method` | CharField | `prime_cost`, `diminishing_value` |
| `rate` | DecimalField | Depreciation rate % |
| `opening_value` | DecimalField | |
| `depreciation_amount` | DecimalField | Calculated for this year |
| `closing_value` | DecimalField | |
| `is_posted` | BooleanField | Whether posted to TB |

#### `StockItem`
Stock on hand items.

---

### 4.14 `core` App — Partnership Models

#### `PartnershipAllocation`
Partnership profit/loss allocation for a financial year.

#### `PartnerShare`
Individual partner's share percentage.

#### `PartnerCapitalAccount`
Partner capital account balances.

---

### 4.15 `core` App — Compliance Document Models

#### `DividendEvent`
Records a dividend declaration event.

#### `DividendShareholderAllocation`
Individual shareholder allocations within a dividend event.

#### `EngagementLetterConfig`
Entity-level engagement letter configuration (APES 305 compliant).

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `entity` | OneToOneField → Entity | |
| `services_engaged` | JSONField | List: `['tax_return', 'financial_statements', 'bas', ...]` |
| `fee_amount` | DecimalField | |
| `fee_basis` | CharField | `fixed`, `hourly`, `value_based` |
| `additional_terms` | TextField | |
| `last_generated_fy` | FK → FinancialYear | |

---

### 4.16 `core` App — Workpaper & Import Models

#### `WorkpaperNote`
Workpaper notes attached to a financial year (can be carried forward).

#### `EntityImportJob`
Tracks bulk entity import jobs.

#### `TaxReferenceData`
Tax rates, thresholds, and reference data by year.

---

### 4.17 `core` App — Office Admin Models (in `models_office_admin.py`)

#### `Correspondence`
Tracks incoming/outgoing correspondence for the firm.

#### `ASICReturn`
Tracks ASIC annual return lodgement status per entity.

#### `NOARecord`
Tracks Notice of Assessment records.

#### `DebtorRecord`
Tracks client debtors (aged receivables).

#### `PaymentPlan`
Tracks ATO payment plans for clients.

#### `DailyTask`
Recurring daily tasks for office admin staff.

#### `DailyTaskCompletion`
Completion records for daily tasks.

---

### 4.18 `review` App Models

#### `ReviewJob`
A bank statement processing job.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `airtable_record_id` | CharField | Optional Airtable sync ID |
| `entity` | FK → Entity | |
| `client_name` | CharField | |
| `file_name` | CharField | |
| `submitted_by` | CharField | |
| `source` | CharField | `upload`, `airtable` |
| `status` | CharField | `awaiting_review`, `in_progress`, `completed` |
| `bank_name` | CharField | Detected bank |
| `account_number` | CharField | |
| `bsb` | CharField | |
| `period_start` | DateField | |
| `period_end` | DateField | |
| `opening_balance` | DecimalField | |
| `closing_balance` | DecimalField | |

#### `PendingTransaction`
A single transaction from a bank statement awaiting review.

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `job` | FK → ReviewJob | |
| `date` | CharField | |
| `description` | CharField | |
| `amount` | DecimalField | Gross amount (inc GST) |
| `tax_type` | CharField | GST classification |
| `confirmed_code` | CharField | Approved account code |
| `confirmed_gst_amount` | DecimalField | |
| `confirmed_net_amount` | DecimalField | |
| `creditable_percentage` | DecimalField | For apportionment |
| `is_confirmed` | BooleanField | |
| `ai_suggested_code` | CharField | AI suggestion |
| `ai_suggested_tax_type` | CharField | |
| `ai_confidence` | FloatField | |
| `rule_applied` | FK → ClassificationRule | |
| `is_split` | BooleanField | |
| `parent_transaction` | FK → self | For split transactions |
| `gst_override` | BooleanField | Manual GST override flag |

#### `ClassificationRule`
Entity-specific classification rule memory (learned from accountant approvals).

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | UUIDField (PK) | |
| `entity` | FK → Entity | |
| `match_type` | CharField | `exact`, `contains` |
| `description_pattern` | CharField | Pattern to match |
| `account_code` | CharField | |
| `tax_type` | CharField | |
| `creditable_percentage` | DecimalField | |
| `is_active` | BooleanField | |
| `hit_count` | IntegerField | How many times applied |

#### `EntityGSTSetting`
Entity-level GST settings for bank statement review.

#### `TransactionPattern`
Learned transaction patterns (legacy, superseded by ClassificationRule).

#### `ReviewActivity`
Activity log for review jobs.

---

### 4.19 `integrations` App Models

#### `AccountingConnection`
Per-entity connection to an accounting software provider (Xero, MYOB, QuickBooks).

#### `ImportLog`
Log of each trial balance import from a cloud accounting system.

#### `XPMConnection`
Connection to Xero Practice Manager.

#### `XPMSyncLog`
Log of each XPM sync operation.

#### `XeroGlobalConnection`
Firm-wide Xero OAuth connection (global, not per-entity).

#### `XeroTenant`
Individual Xero organisations (tenants) linked to the global connection.

#### `QBGlobalConnection`
Firm-wide QuickBooks Online OAuth connection.

#### `QBTenant`
Individual QuickBooks companies linked to the global connection.

#### `MYOBGlobalConnection`
MYOB connection (currently removed/disabled — `_myob_removed()` stub in views).

#### `MYOBCompanyFile`
MYOB company files (legacy).

---

## 5. User Roles & Permissions

| Role | Code | Can View All Entities | Can Finalise | Can Do Accounting | Can Edit | Notes |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| Administrator | `admin` | ✓ | ✓ | ✓ | ✓ | Full access |
| Senior Accountant | `senior_accountant` | ✓ | ✓ | ✓ | ✓ | |
| Accountant | `accountant` | ✗ | ✗ | ✓ | ✓ | Sees assigned entities only |
| Office Admin | `office_admin` | ✓ | ✗ | ✗ | ✗ | No accounting work |
| Read Only | `read_only` | ✗ | ✗ | ✗ | ✗ | View only |

**Permission Properties on User model:**
- `is_admin` — role == admin
- `is_office_admin` — role == office_admin
- `is_senior` — role in (admin, senior_accountant)
- `can_view_all_entities` — admin, senior_accountant, or office_admin
- `can_finalise` — admin or senior_accountant
- `can_do_accounting` — admin, senior_accountant, or accountant
- `can_edit` — admin, senior_accountant, or accountant

**2FA:** All users are required to complete TOTP 2FA setup before accessing the platform. The `Require2FAMiddleware` enforces this. TOTP secrets are encrypted at rest using the `cryptography` library.

---

## 6. Financial Year Workflow & Lifecycle

### Status Flow

```
draft
  ↓ (accountant begins work)
in_review
  ↓ (accounting work complete)
finished
  ↓ (submit for Eva review)
prepared / pending_eva
  ↓ (Eva runs 14 compliance checks)
eva_cleared  ←→  eva_error
  ↓ (senior accountant locks)
locked
```

### Key Operations

| Operation | View | Description |
| :--- | :--- | :--- |
| Create FY | `financial_year_create` | Creates new FY for entity |
| Roll Forward | `roll_forward` | Creates new FY from prior year, carries forward TB balances as opening balances |
| Re-roll Forward | `reroll_forward` | Re-creates roll-forward from updated prior year |
| Reopen | `reopen_financial_year` | Reopens a locked/finalised FY (senior only) |
| Status Change | `financial_year_status` | Manual status transitions |
| Delete Unfinalised | `delete_unfinalised_fy` | Deletes a draft/in-review FY |

### Comparative Periods

Comparative (prior year) figures are populated via `populate_comparatives`, which copies the prior year's closing balances. Comparatives can be locked (`lock_comparatives`) to prevent changes, and individual lines can be manually overridden (`override_comparative`).

---

## 7. Data Import Methods — All Pathways

StatementHub supports **seven distinct data import pathways** for trial balance data, plus bank statement imports.

### 7.1 CSV / Excel Trial Balance Import (Manual Upload)

**URL:** `trial_balance_import` → `review_tb_import` → `commit_tb_import`  
**View:** `core/views.py` — `trial_balance_import()`, `_process_trial_balance_upload()`, `_parse_tb_excel()`  
**Template download:** `trial_balance_template_download`

**Process:**
1. Accountant uploads a CSV or Excel (.xlsx) file
2. `_process_trial_balance_upload()` detects file type and calls `_parse_tb_excel()` for Excel or CSV parser for CSV
3. System attempts to auto-map account codes using `_apply_tb_learned_mappings()` (checks `ClientAccountMapping` for prior mappings)
4. Preview shown via `review_tb_import` — accountant can review and correct mappings
5. `commit_tb_import` creates `TrialBalanceLine` records and applies learned mappings

**Expected columns:** Account Code, Account Name, Debit, Credit (plus optional Prior Year Debit/Credit, Opening Balance)

**Learned Mappings:** After each import, the system remembers account code → AccountMapping associations per entity, so subsequent imports auto-map previously seen codes.

### 7.2 Access Ledger Import (Legacy Software Migration)

**URL:** `access_ledger_import`  
**View:** `core/views.py` — `access_ledger_import()`  
**Service:** `core/access_ledger_import.py`  
**Management command:** `core/management/commands/import_access_ledger.py`

**Process:**
1. Accepts an Access Ledger export file (proprietary format)
2. `access_ledger_import.py` parses the file and extracts trial balance data
3. Creates entities, financial years, and trial balance lines
4. Can be run as a management command for bulk migration: `python manage.py import_access_ledger`

This is the primary migration pathway from the firm's legacy `Access Ledger` on-premise software.

### 7.3 Xero Import (Cloud Accounting)

**URL:** `integrations:import_from_cloud` → `integrations:xero_select_tenant_import` → `integrations:review_import` → `integrations:commit_import`  
**View:** `integrations/views.py` — `import_from_cloud()`, `_do_cloud_import()`, `review_import()`, `commit_import()`

**Process:**
1. Entity must have an `AccountingConnection` linked to a `XeroTenant`
2. OAuth token is refreshed if needed (`_ensure_valid_token()`)
3. Xero API is called to fetch trial balance data for the selected period
4. Data is mapped using `_apply_learned_mappings()` (entity-specific account code memory)
5. Preview shown for accountant review
6. On commit, `TrialBalanceLine` records are created

**Global Xero Connection:** The firm can also connect a global Xero account (`xero_global_dashboard`) that covers all tenants, enabling rapid import across multiple clients.

**Rapid Import Mode:** `xero_stop_rapid` — stops a rapid multi-entity import in progress.

### 7.4 QuickBooks Online Import

**URL:** `integrations:qb_select_tenant_import` → `integrations:review_import` → `integrations:commit_import`  
**View:** `integrations/views.py` — `qb_select_tenant_import()`, `_ensure_qb_tenant_token()`

Same process as Xero but via QuickBooks Online API. Uses `QBGlobalConnection` and `QBTenant` models.

### 7.5 MYOB Import (Removed)

MYOB integration has been removed. The `_myob_removed()` stub in `integrations/views.py` returns an error message if accessed. `MYOBGlobalConnection` and `MYOBCompanyFile` models remain in the database for historical data.

### 7.6 HandiLedger Import

**URL:** `entity_import_handiledger`  
**View:** `core/views.py` — `entity_import_handiledger()`

Imports from HandiLedger (Sage/HandiSoft) export format. Entity-level import.

### 7.7 Bulk Entity Import (Excel Template)

**URL:** `bulk_import_start` → `bulk_import_map` → `bulk_import_validate` → `bulk_import_execute`  
**View:** `core/views_upgrades.py`  
**Template download:** `bulk_import_template`

**Process:**
1. Accountant downloads the bulk import Excel template
2. Fills in multiple entities' trial balance data in a single workbook
3. Uploads the file — system maps columns (`bulk_import_map`)
4. Validates data (`bulk_import_validate`) — shows errors/warnings
5. Executes import (`bulk_import_execute`) — creates entities, financial years, and TB lines in bulk

Used for onboarding large numbers of new clients simultaneously.

### 7.8 XRM Pull (Xero Practice Manager Client Sync)

**URL:** `xrm_search`, `xrm_pull`  
**View:** `core/views.py` — `xrm_search()`, `xrm_pull()`

Pulls client/entity data from Xero Practice Manager (XPM) into StatementHub. This is a client data sync (names, addresses, ABNs), not a trial balance import.

### 7.9 Journal Upload (Excel Bulk Journals)

**URL:** `journal_upload` → `review_journal_upload` → `commit_journal_upload`  
**View:** `core/views.py` — `journal_upload()`, `_parse_journal_excel()`, `review_journal_upload()`, `commit_journal_upload()`  
**Template download:** `journal_template_download`

**Process:**
1. Accountant downloads the journal upload Excel template
2. Fills in journal entries (Date, Account Code, Account Name, Debit, Credit, Description)
3. Uploads file — system parses and applies learned mappings (`_apply_journal_learned_mappings()`)
4. Preview shown for review
5. On commit, creates `AdjustingJournal` and `JournalLine` records, and applies each line to the trial balance

### 7.10 Bank Statement Import (Review App)

**URL:** `review:upload_statement` → `review:parse_statement` → `review:upload_preview` → `review:confirm_import`  
**View:** `review/views.py` — `upload_bank_statement()`, `parse_statement()`, `upload_preview()`, `confirm_import()`

**Supported file types:** PDF (bank-specific parsers), Excel (.xlsx)

**Supported banks (PDF):**
- Commonwealth Bank (CBA) — standard statement format
- Commonwealth Bank (CBA) — Transaction Listing (NetBank export)
- ANZ
- Westpac
- Bank of Melbourne (Westpac subsidiary)
- NAB (National Australia Bank)
- ING Bank (Savings Maximiser)
- Macquarie Bank
- Bendigo Bank

**Process:**
1. File uploaded to `upload_bank_statement()`
2. Bank auto-detected via `detect_bank()` using text pattern matching on first page
3. Bank-specific parser extracts: opening balance, closing balance, account name, BSB, account number, period dates, and individual transactions
4. For Excel files, `_parse_excel_bank_statement()` handles generic Excel format
5. Preview shown — accountant reviews extracted transactions
6. On confirm, `ReviewJob` and `PendingTransaction` records created
7. AI classification runs (`review_classify_ai`) — LLM suggests account codes and GST treatment
8. Accountant reviews, approves/overrides each transaction
9. Approved transactions post to trial balance via `_post_confirmed_txn_to_tb()`

**Airtable Integration:** Bank statements can also be sourced from Airtable (`_sync_from_airtable()`). When `AIRTABLE_API_KEY` is configured, the system pulls pending review records from Airtable and creates `ReviewJob` records.

---

## 8. Eva AI — Knowledge, Interactions & Processes

Eva is the AI assistant embedded throughout StatementHub. She has multiple distinct operational modes, each with its own context-building pipeline and LLM prompt strategy.

### 8.1 Eva Architecture Overview

Eva is built on a **Retrieval-Augmented Generation (RAG)** architecture:

1. **Knowledge Brain** — A vector store of firm documents synced from SharePoint, chunked and embedded using OpenAI `text-embedding-ada-002`
2. **Context Builder** — Assembles financial year data, risk flags, amber indicators, and relevant knowledge chunks into a structured prompt
3. **LLM Layer** — Calls Claude (Anthropic) or OpenAI-compatible models via `ai_service.py`
4. **Streaming** — Eva chat responses are streamed to the browser via Server-Sent Events

### 8.2 LLM Tiers

| Tier | OpenAI-Compatible Model | Anthropic Model | Use Case |
| :--- | :--- | :--- | :--- |
| `haiku` | `gpt-4.1-nano` | `claude-haiku-4-5` | Fast classification, quick checks |
| `sonnet` | `gpt-4.1-mini` | `claude-sonnet-4-6` | Analysis, explanations, chat |
| `opus` | `gpt-4.1-mini` | `claude-opus-4-6` | Complex reports, synthesis |

The active provider is controlled by the `USE_ANTHROPIC` environment variable. When `USE_ANTHROPIC=true`, the Anthropic SDK is used; otherwise the OpenAI-compatible API is used.

### 8.3 Knowledge Brain

**Source:** Microsoft SharePoint (via Microsoft Graph API)  
**Sync frequency:** Every 2 hours (Celery Beat schedule)  
**Manual trigger:** `knowledge_sync` view or `trigger_knowledge_sync`

**Supported file types for ingestion:**
- `.docx` — Word documents (python-docx)
- `.pdf` — PDFs (pdfplumber)
- `.txt` — Plain text
- `.xlsx` — Excel spreadsheets (openpyxl)
- `.pptx` — PowerPoint presentations (python-pptx)
- `.msg` — Outlook email files (extract-msg), including attachments

**Sync Process (`eva_knowledge.py` — `sync_sharepoint_library()`):**
1. Authenticate to Microsoft Graph API using client credentials (tenant ID, client ID, client secret)
2. List all files in the configured SharePoint drive/folder
3. For each file: check if modified since last sync
4. Download changed files, extract text using format-specific parser
5. Chunk text into ~500-token chunks with 50-token overlap (`chunk_text()`)
6. Generate embeddings for each chunk via OpenAI API (`_get_embeddings()`)
7. Store chunks and embeddings in `KnowledgeChunk` records
8. Update `KnowledgeDocument.sync_status` to `synced`

**Semantic Search (`search_knowledge_brain()` / `retrieve_relevant_chunks()`):**
1. Generate embedding for the query
2. Load all non-archived, synced chunks with embeddings
3. Compute cosine similarity between query embedding and each chunk embedding
4. Return top-K chunks (default: 5) sorted by similarity

**Knowledge Brain Admin UI:** `knowledge_brain_admin` view shows all documents, sync status, chunk counts, and allows manual archive/unarchive. `knowledge_documents` lists all documents. `knowledge_search` provides a search interface.

### 8.4 Eva Chat Mode

**URL:** `eva_chat_api` (POST/GET)  
**View:** `core/views_eva.py` — `eva_chat_api()` → dispatches to `eva_chat.py`  
**Module:** `core/eva_chat.py` — `eva_chat_send()`, `eva_chat_history()`, `eva_chat_dispatch()`

**Context Payload (`build_context_payload()` in `eva_chat.py`):**
Eva chat builds a rich context payload including:
- Entity details (name, type, ABN, industry, GST status)
- Financial year details (year label, status, period)
- Trial balance summary (revenue, expenses, assets, liabilities, equity totals)
- Amber indicators (accounts with significant variances)
- Open risk flags (title, severity, description)
- Eva review findings (if a review has been run)
- Relevant knowledge chunks (RAG search against user's message)

**Trust Planning Mode:** When the entity is a trust and the user's message contains trust distribution keywords, Eva switches to trust planning mode (`is_trust_planning_query()`), using a specialised system prompt (`TRUST_PLANNING_SYSTEM_PROMPT`) that focuses on:
- Trust income summary (NDI, income streams)
- Beneficiary tax profiles
- Distribution strategy optimisation
- Section 100A risk
- Division 6AA (minors)
- Trustee resolution preparation

**Conversation Persistence:** Each conversation is stored as `EvaConversation` + `EvaMessage` records. History is loaded on each request and included in the LLM prompt.

**Streaming:** Responses are streamed via `_call_llm_stream()` using Server-Sent Events.

### 8.5 Eva Review Mode (Finalisation Review)

**URL:** `ask_eva_review` (triggers async review), `eva_review_status` (polls), `eva_review_detail` (results)  
**Module:** `core/eva_engine.py`  
**Celery task:** `eva_finalisation_review`

**Purpose:** A structured compliance review run before a financial year is locked. Eva runs 14 compliance checks and produces findings with severity ratings and recommendations.

**Pre-flight Checks (`run_preflight_checks()`):**
Before running the full review, Eva checks:
- Trial balance is balanced (debits = credits)
- At least one TB line exists
- All accounts are mapped to financial statement lines
- No suspense account balances
- Prior year comparatives are populated (if applicable)

**The 14 Compliance Checks:**

| Check ID | Name | Applicable Entities |
| :--- | :--- | :--- |
| `div7a` | Division 7A Loan Compliance | company, trusts |
| `gst_reconciliation` | GST Reconciliation | all |
| `related_party` | Related Party Transactions | company, trusts, partnership |
| `smsf_compliance` | SMSF Compliance (SIS Act) | smsf |
| `trust_distribution` | Trust Distribution Resolution | trusts |
| `depreciation_review` | Depreciation Schedule Review | all |
| `tb_integrity` | Trial Balance Integrity | all |
| `comparative_consistency` | Comparative Period Consistency | all except individual |
| `super_guarantee` | Superannuation Guarantee Compliance | company, trusts, partnership, sole_trader |
| `ato_benchmarks` | ATO Industry Benchmarks | company, trusts, partnership, sole_trader |
| `going_concern` | Going Concern Assessment | company, trusts, partnership, smsf |
| `tpar` | Taxable Payments Annual Report | company, trusts, partnership, sole_trader |
| `thin_capitalisation` | Thin Capitalisation | company, trusts, partnership |

**Review Process (`_run_eva_review_background()`):**
1. Run pre-flight checks — abort if critical failures
2. Run risk engine pre-check (`_run_risk_engine_precheck()`) — collect existing risk flags
3. Run dedicated detection modules (`_collect_module_flags()`) — Div7A, Going Concern, Section 100A, Related Party cluster, SGC cluster, TPAR cluster
4. For each applicable compliance check:
   a. Build check-specific context (`_build_check_context()`) — includes TB data, risk flags, module findings
   b. Retrieve relevant Knowledge Brain chunks (RAG)
   c. Call LLM with structured prompt requesting JSON output
   d. Parse LLM response (`_parse_llm_json()`) with fallback repair (`_repair_truncated_json()`)
   e. Create `EvaFinding` records
5. Link cross-referenced findings
6. Update `EvaReview.status` to `completed`
7. Update `FinancialYear.status` to `eva_cleared` or `eva_error`

**Finding Resolution:** `eva_resolve_finding` — accountant can mark a finding as resolved with notes.

**Re-run:** `eva_rerun_review` — re-runs the full review (creates a new `EvaReview` record).

**Finalise:** `eva_finalise` — locks the financial year after Eva review is cleared.

### 8.6 Amber Indicators

**Module:** `core/eva_amber.py` — `compute_amber_indicators()`

Amber indicators are trial balance variance flags computed for every account line. They appear in the TB view and are included in Eva's context.

**Six trigger conditions:**
1. **Significant variance (%)** — >15% for revenue accounts, >20% for expense accounts
2. **Account dropped** — non-zero prior year, zero current year
3. **Account added** — non-zero current year, no prior year
4. **Opening balance mismatch** — prior year closing ≠ current year opening
5. **Balance sign change** — account flipped from debit to credit or vice versa
6. **Materiality threshold** — absolute dollar variance exceeds section materiality

### 8.7 Eva Division 7A Assessment

**Module:** `core/eva_div7a.py` — `run_div7a_assessment()`  
**Celery task:** `div7a_assessment`

Runs 8 specific Div 7A detection rules:

| Rule | Title |
| :--- | :--- |
| `T2-D7A-01` | Shareholder/Director Loan Debit Balance |
| `T2-D7A-02` | Loan Balance Increase (Escalation Modifier) |
| `T2-D7A-03` | Payments to/for Shareholders (s 109E) |
| `T2-D7A-04` | Missing Complying Loan Agreement |
| `T2-D7A-05` | Missing Benchmark Interest Income |
| `T2-D7A-06` | Minimum Yearly Repayment Shortfall |
| `T2-D7A-07` | Unpaid Present Entitlements (Trust → Company) |
| `T2-D7A-08` | Interposed Entity Loans (ss 109T–109V) |

Produces a `Div7AAssessment` record with findings and severity.

### 8.8 Eva BAS Commentary

**Module:** `core/eva_bas_commentary.py` — `generate_bas_commentary()`  
**Celery task:** `eva_bas_commentary`  
**View:** `core/views_bas_commentary.py`

Generates AI-powered period commentary for a BAS period, transforming compliance data into client-ready advisory insights.

**Context built includes:**
- GST figures (1A, 1B, net GST)
- Period-on-period comparison (prior quarter/month)
- Year-on-year comparison (same period prior year)
- Top expense categories
- Revenue trends
- Notable transactions

**Output:** Structured JSON with sections (summary, key movements, compliance notes, recommendations) rendered to a Word document (.docx).

**Tones:** `professional`, `conversational`, `technical`

**Workflow:** Generate → Draft → Review → Mark Sent. Can compare two versions (`compare_commentaries`).

### 8.9 Eva Client Summary

**Module:** `core/eva_client_summary.py` — `generate_client_summary()`  
**Celery task:** `eva_client_summary`  
**View:** `core/views_client_summary.py`

Generates a five-section client summary when a financial year is locked.

**Five sections:**
1. Financial Highlights
2. Compliance Status
3. Tax Position
4. Recommendations
5. Year-on-Year Comparison

**Formats:** `bullet` (bullet points) or `narrative` (prose paragraphs).

### 8.10 Eva Proactive Suggestions

**Module:** `core/eva_proactive.py` — `generate_proactive_suggestion()`  
**Celery task:** `eva_proactive_suggestion`

Generates proactive suggestions triggered by specific events (e.g. status change, new import, risk flag created).

### 8.11 Eva Trust Planning Mode

**Module:** `core/eva_trust_planning.py`

Activated when:
- Entity type is `trust`
- User's message contains trust distribution keywords (detected by `is_trust_planning_query()`)

**Context built includes:**
- Trust income summary (total NDI, income streams broken down by type)
- Beneficiary profiles (type, marginal rate, other income, bracket remaining)
- Entity info (trust type, trustee, year end)
- Existing distribution scenarios
- Compliance flags (Section 100A risk, Division 6AA)

**System prompt capabilities:**
- Summarise trust income position
- Profile beneficiary tax positions
- Recommend optimal distribution strategy
- Flag Section 100A and Division 6AA risks
- Support "what if" scenario modelling
- Pre-populate draft trustee resolution

---

## 9. Risk Engine — All Rules & Detection Modules

### 9.1 Risk Engine Architecture

The risk engine (`core/risk_engine.py`) runs in two tiers:

**Tier 1 — Variance Analysis (`_run_tier1_variance()`):**
Automatically flags accounts with significant year-on-year variances based on materiality thresholds. Also runs `_check_div7a_loans()` and `_check_aggregate_variances()`.

**Tier 2 — Rule-Based Detection (`_evaluate_tier2_rule()`):**
Evaluates each seeded `RiskRule` against the trial balance data. Supports 7 trigger types:
- `account_threshold` — account balance exceeds threshold
- `ratio_check` — financial ratio check
- `balance_sign` — account has unexpected sign
- `solvency` — solvency indicators
- `loan_check` — loan-related checks
- `gst_check` — GST-related checks
- `superannuation` — super guarantee checks
- `trust_distribution` — trust distribution checks
- `expense_benchmark` — ATO benchmark comparison

**Dedicated Detection Modules (run before Tier 2):**
Registered in `core/risk_modules/registry.py`. These replace corresponding Tier 2 rules.

| Module | Class | Replaces |
| :--- | :--- | :--- |
| `core.risk_modules.div7a.Div7ADetectionModule` | Div7A | D7A-*, T2-D7A-* |
| `core.risk_modules.going_concern.GoingConcernModule` | Going Concern | SOL-* |
| `core.risk_modules.section100a.Section100AModule` | Section 100A | TRU-* |
| `core.risk_modules.cluster_rp.RelatedPartyCluster` | Related Party | RP-* |
| `core.risk_modules.cluster_sgc.SGCCluster` | Super Guarantee | SG-* |
| `core.risk_modules.cluster_tpar.TPARCluster` | TPAR | (new capability) |

### 9.2 Complete Risk Rule Catalogue

#### Division 7A (Legacy — D7A series, replaced by T2-D7A module)

| Rule ID | Title | Severity |
| :--- | :--- | :--- |
| D7A-01 | Director/shareholder loan — Div 7A risk | CRITICAL |
| D7A-02 | Div 7A — Minimum repayment not met | HIGH |
| D7A-03 | Company paying private expenses | HIGH |
| D7A-04 | Intercompany loan — Div 7A interposed entity | HIGH |
| D7A-05 | Unpaid present entitlement — trust to company | HIGH |
| D7A-06 | Div 7A benchmark interest not charged | MEDIUM |

#### Superannuation Guarantee (Legacy — SG series, replaced by SGC module)

| Rule ID | Title | Severity |
| :--- | :--- | :--- |
| SG-01 | Superannuation guarantee shortfall | CRITICAL |
| SG-02 | Contractor payments — SG obligation check | HIGH |
| SG-03 | Director fees without super | HIGH |
| SG-04 | Super paid to wrong fund or late | HIGH |
| SG-05 | Concessional contribution cap exceeded (SMSF) | MEDIUM |

#### GST

| Rule ID | Title | Severity |
| :--- | :--- | :--- |
| GST-01 | GST claimed exceeds benchmark ratio | HIGH |
| GST-02 | Unclassified bank transactions pending | MEDIUM |
| GST-03 | GST on capital purchases not separately reported | MEDIUM |
| GST-04 | Revenue below GST registration threshold | LOW |
| GST-05 | Input-taxed supplies detected — apportionment required | MEDIUM |
| GST-06 | GST liability balance carried forward | MEDIUM |

#### Solvency (Legacy — SOL series, replaced by Going Concern module)

| Rule ID | Title | Severity |
| :--- | :--- | :--- |
| SOL-01 | Current ratio below 1.0 — solvency concern | HIGH |
| SOL-02 | Net asset deficiency | HIGH |
| SOL-03 | Accumulated losses exceed paid-up capital | HIGH |
| SOL-04 | Overdue ATO liabilities | HIGH |

#### Expenses

| Rule ID | Title | Severity |
| :--- | :--- | :--- |
| EXP-01 | Motor vehicle expenses exceed ATO benchmark | MEDIUM |
| EXP-02 | Travel expenses exceed ATO benchmark | MEDIUM |
| EXP-03 | Entertainment expenses — deductibility review | MEDIUM |
| EXP-04 | Contractor payments exceed ATO benchmark | MEDIUM |
| EXP-05 | Rent expenses exceed ATO benchmark | MEDIUM |
| EXP-06 | Depreciation — immediate write-off review | LOW |
| EXP-07 | Bad debts written off — substantiation required | MEDIUM |
| EXP-08 | Repairs vs capital improvements | MEDIUM |
| EXP-09 | Legal fees — capital vs revenue | MEDIUM |
| EXP-10 | Donations — DGR status required | LOW |

#### Capital Gains Tax

| Rule ID | Title | Severity |
| :--- | :--- | :--- |
| CGT-01 | Capital gain detected — CGT event review | HIGH |
| CGT-02 | Capital loss carried forward | MEDIUM |
| CGT-03 | Property disposal — CGT and GST interaction | HIGH |
| CGT-04 | Small business CGT concession eligibility | MEDIUM |
| CGT-05 | Crypto/digital asset disposal | MEDIUM |

#### Trust (Legacy — TRU series, replaced by Section 100A module)

| Rule ID | Title | Severity |
| :--- | :--- | :--- |
| TRU-01 | Trust income not fully distributed | HIGH |
| TRU-02 | Section 100A — reimbursement agreement risk | HIGH |
| TRU-03 | Trust deed review — distribution powers | HIGH |
| TRU-04 | Trust loss carry-forward — trust loss provisions | HIGH |
| TRU-05 | Trustee remuneration — trust deed authority | LOW |
| TRU-06 | SMSF — in-house asset rule | HIGH |

#### Related Party (Legacy — RP series, replaced by RP cluster module)

| Rule ID | Title | Severity |
| :--- | :--- | :--- |
| RP-01 | Related party transactions detected | MEDIUM |
| RP-02 | Management fees to related entities | MEDIUM |
| RP-03 | Rent paid to related parties | LOW |
| RP-04 | SMSF — related party acquisition | CRITICAL |
| RP-05 | Loans between related entities | MEDIUM |

#### Fringe Benefits Tax

| Rule ID | Title | Severity |
| :--- | :--- | :--- |
| FBT-01 | Fringe benefits detected — FBT return required | HIGH |
| FBT-02 | Motor vehicle — FBT car benefit | MEDIUM |
| FBT-03 | Employee loans — FBT loan benefit | MEDIUM |
| FBT-04 | Entertainment — FBT meal entertainment | LOW |

#### General

| Rule ID | Title | Severity |
| :--- | :--- | :--- |
| GEN-01 | Suspense account has balance | HIGH |
| GEN-02 | Unmapped accounts in trial balance | MEDIUM |
| GEN-03 | Revenue accounts with debit balances | MEDIUM |
| GEN-04 | Asset accounts with credit balances | MEDIUM |
| GEN-05 | Trial balance does not balance | CRITICAL |
| GEN-06 | Prior year adjustments detected | LOW |
| GEN-07 | Negative bank balance — potential overdraft | LOW |
| GEN-08 | Provision for income tax review | LOW |
| GEN-09 | Large rounding or adjustment entries | LOW |

#### SMSF

| Rule ID | Title | Severity |
| :--- | :--- | :--- |
| SMSF-01 | SMSF — sole purpose test | CRITICAL |
| SMSF-02 | SMSF — non-concessional contribution cap | HIGH |
| SMSF-03 | SMSF — LRBA (limited recourse borrowing) | HIGH |
| SMSF-04 | SMSF — pension payments compliance | HIGH |
| SMSF-05 | SMSF — investment strategy review | MEDIUM |

#### Division 7A (New — T2-D7A series, from dedicated module)

| Rule ID | Title | Severity |
| :--- | :--- | :--- |
| T2-D7A-01 | Shareholder/Director Loan Debit Balance | CRITICAL |
| T2-D7A-02 | Loan Balance Increase (Escalation Modifier) | HIGH |
| T2-D7A-03 | Payments to/for Shareholders (s 109E) | HIGH |
| T2-D7A-04 | Missing Complying Loan Agreement | CRITICAL |
| T2-D7A-05 | Missing Benchmark Interest Income | HIGH |
| T2-D7A-06 | Minimum Yearly Repayment Shortfall | CRITICAL |
| T2-D7A-07 | Unpaid Present Entitlements (Trust → Company) | HIGH |
| T2-D7A-08 | Interposed Entity Loans (ss 109T–109V) | CRITICAL |

#### Going Concern (New — GC series, from dedicated module)

| Rule ID | Title | Severity |
| :--- | :--- | :--- |
| GC-01 | Net Liability Position | CRITICAL |
| GC-02 | Cash Position Assessment | HIGH |
| GC-03 | Revenue Decline Trajectory | HIGH |
| GC-04 | Consecutive Losses | HIGH |
| GC-05 | Working Capital Ratio | HIGH |
| GC-06 | Director Loan Extraction Relative to Operations | MEDIUM |

#### Section 100A (New — S100A series, from dedicated module)

| Rule ID | Title | Severity |
| :--- | :--- | :--- |
| S100A-01 | Distribution to Low-Tax Beneficiary | HIGH |
| S100A-02 | Circular Money Flow | CRITICAL |
| S100A-03 | UPE to Related Entity | HIGH |
| S100A-04 | Resolution Date Compliance | CRITICAL |
| S100A-05 | Four-Factor Summary Assessment | HIGH |

#### Related Party Cluster (New — RP-C series, from dedicated module)

| Rule ID | Title | Severity |
| :--- | :--- | :--- |
| RP-C01 | Inter-Entity Balance Detection (AASB 124) | MEDIUM |
| RP-C02 | KMP Transaction Detection | MEDIUM |
| RP-C03 | Arm's Length Assessment | MEDIUM |

#### SGC Cluster (New — SGC series, from dedicated module)

| Rule ID | Title | Severity |
| :--- | :--- | :--- |
| SGC-01 | SG Rate Shortfall | CRITICAL |
| SGC-02 | Contractor SG Exposure | HIGH |
| SGC-03 | SG Charge Risk | HIGH |

#### TPAR Cluster (New — TPAR series, from dedicated module)

| Rule ID | Title | Severity |
| :--- | :--- | :--- |
| TPAR-01 | TPAR Industry Detection | ADVISORY |
| TPAR-02 | Contractor Payment Threshold | ADVISORY |

### 9.3 AI-Powered Risk Analysis

**`ai_analyse_flag`** — Analyses a single risk flag using the LLM (`ai_service.py` — `analyse_risk_flag()`). Produces a narrative analysis and priority score (1–10).

**`ai_analyse_all_flags`** — Batch analyses all open flags for a financial year (`batch_analyse_flags()`). Uses deduplication hash to avoid re-analysing unchanged flags.

**`ai_prioritise_flags`** — Asks the LLM to prioritise all open flags and return a ranked list with rationale (`prioritise_flags()`).

**`generate_risk_report`** — Generates a Word document risk summary report (`generate_risk_summary_report()`).

**`ai_feedback`** — Records accountant feedback (thumbs up/down) on AI analyses to improve future responses.

---

## 10. Document Generation — All Document Types

### 10.1 Financial Statements (`docgen.py`)

Generated as Word (.docx) documents, optionally converted to PDF via LibreOffice.

**Components generated:**
- Cover page
- Directors' Report (companies)
- Statement of Financial Position (Balance Sheet)
- Statement of Profit or Loss (Income Statement)
- Statement of Changes in Equity
- Statement of Cash Flows (if applicable)
- Notes to the Financial Statements

**Watermarking:** If open risk flags exist, the document is watermarked "DRAFT — UNRESOLVED RISK FLAGS".

**Download formats:** `.docx` or `.pdf` (via LibreOffice headless conversion)

### 10.2 Management Accounts (`mgmt_accounts.py`)

Period-scoped, watermarked management accounts for interim reporting.

**Sources:** Can be generated from:
- Existing trial balance data (`build_manual_tb_sections()`)
- Cloud accounting system (Xero/QuickBooks) via transient TB fetch (`fetch_transient_tb_from_cloud()`)
- Bank-derived TB (`build_bank_derived_tb()`) — constructed from approved bank statement transactions

**Components:** Cover page + Balance Sheet + P&L (always watermarked as management accounts)

### 10.3 Legal & Compliance Documents (35 types via `LegalDocumentTemplate`)

All generated using docxtpl (Word template rendering). Templates are uploaded by admins and stored in the database.

#### Legal Documents
| Type | Description |
| :--- | :--- |
| `div7a_loan_agreement` | Div 7A Loan Agreement |
| `trust_deed_change_trustee` | Trust Deed — Change Trustee |
| `trust_deed_add_beneficiary` | Trust Deed — Add Beneficiary |
| `trust_deed_remove_beneficiary` | Trust Deed — Remove Beneficiary |
| `trust_deed_extend_vesting` | Trust Deed — Extend Vesting |
| `trust_deed_update_distribution` | Trust Deed — Update Distribution |
| `company_constitution` | Company Constitution |
| `company_constitution_special` | Company Constitution — Special Purpose |
| `company_establishment` | Company Establishment Package |
| `discretionary_trust_deed` | Discretionary Trust Deed |
| `unit_trust_deed` | Unit Trust Deed |
| `unit_trust_deed_ancillaries` | Unit Trust Deed — Ancillary Documents |
| `unit_transfer` | Unit Transfer Package |
| `partnership_agreement` | Partnership Agreement |

#### Compliance Documents
| Type | Description |
| :--- | :--- |
| `dividend_statement` | Dividend Statement |
| `dividend_minutes` | Dividend Declaration Minutes |
| `solvency_resolution` | Solvency Resolution |
| `directors_declaration` | Director's Declaration |
| `directors_declaration_large` | Director's Declaration — Large Proprietary |
| `directors_declaration_gp` | Director's Declaration — General Purpose |
| `directors_report` | Director's Report (Eva-assisted drafting) |
| `shareholder_loan_ack` | Shareholder Loan Acknowledgment |
| `partner_statement` | Partner Statement |
| `partnership_tax_summary` | Partnership Tax Summary |
| `engagement_letter` | Client Engagement Letter (APES 305) |
| `management_rep_letter` | Management Representation Letter |
| `management_rep_letter_trust` | Management Representation Letter — Trust |
| `management_rep_letter_partnership` | Management Representation Letter — Partnership |
| `client_cover_letter` | Client Cover Letter |
| `distribution_minutes` | Trust Distribution Minutes |
| `section_100a_summary` | Section 100A Summary |

### 10.4 Distribution Minutes (`distmin_gen.py`)

Trust distribution minutes generated from the trust distribution workspace data.

### 10.5 Tax Planning Summary (`taxplan_docgen.py`)

Tax planning summary document generated from the tax planning worksheet.

### 10.6 BAS Commentary Document (`eva_bas_commentary.py` — `generate_commentary_docx()`)

Word document containing the AI-generated BAS period commentary.

### 10.7 Risk Summary Report (`ai_service.py` — `generate_risk_summary_report()`)

Word document summarising all risk flags for a financial year with AI analysis.

### 10.8 Trial Balance Documents

- **PDF:** `trial_balance_pdf` — generates a formatted PDF of the trial balance
- **Word:** `trial_balance_download` — generates a Word document of the trial balance
- **Excel:** `trial_balance_download` — generates an Excel workbook of the trial balance

### 10.9 Journals PDF

`journals_pdf` — generates a PDF of all journals for a financial year.

### 10.10 Client Package Assembly (`package_service.py`)

Assembles a complete client package by combining:
- Financial statements
- All relevant compliance documents (solvency resolution, directors' declaration, etc.)
- Cover letter
- Management representation letter

Sent for e-signing via FuseSign API.

---

## 11. BAS / GST Module

### 11.1 Overview

The BAS module (`views_bas.py`, `bas_utils.py`) calculates GST activity statement figures from the trial balance.

**GST Labels calculated:**
- **1A** — GST on Sales (GST collected)
- **1B** — GST on Purchases (input tax credits)
- **Net GST** — 1A minus 1B

### 11.2 BAS Period Management

- Periods are created lazily when first accessed
- `bas_lodge_period` — marks a period as lodged and takes a snapshot of 1A/1B figures
- `bas_unlodge_period` — reverses lodgement (with audit trail)
- `bas_coverage_check` — checks which transactions in the period are classified

### 11.3 Transaction Reallocation

- `bas_reallocate_transaction` — reallocates a single transaction to a different GST code
- `bas_bulk_reallocate` — bulk reallocates multiple transactions

### 11.4 Download Formats

- **Excel:** Period-by-period BAS workbook with detail sheet
- **PDF:** Formatted BAS summary (WeasyPrint)
- Both support single period and all-periods download

### 11.5 AI Commentary

See Section 8.8 — Eva BAS Commentary.

---

## 12. Bank Statement Review Module

### 12.1 Overview

The `review` app handles the complete bank statement processing workflow, from PDF/Excel upload through AI classification to trial balance posting.

### 12.2 Upload & Parsing

- **PDF:** Bank-specific parsers in `pdf_parsers.py` (8 banks + 1 CBA variant = 9 parsers)
- **Excel:** Generic Excel parser `_parse_excel_bank_statement()`
- **Airtable sync:** `_sync_from_airtable()` pulls pending records from Airtable

### 12.3 AI Classification

`review_classify_ai` — triggers AI classification for all unclassified transactions in a financial year.

**Process (`_run_ai_classification_background()` in `core/views.py`):**
1. Loads all unconfirmed transactions for the entity
2. Loads entity's `ClassificationRule` records
3. For each transaction: first checks if any rule matches (exact or contains)
4. If no rule matches: calls LLM with transaction description + entity context
5. LLM suggests: account code, tax type, confidence score
6. Results stored in `ai_suggested_code`, `ai_suggested_tax_type`, `ai_confidence`

**Classification status polling:** `review_classify_status` — returns JSON with progress.

### 12.4 Transaction Review & Approval

- `review_approve_transaction` — approves a single transaction (sets `is_confirmed=True`, posts to TB)
- `review_unconfirm_transaction` — reverses an approval (removes from TB)
- `review_approve_all` — approves all AI-suggested transactions
- `review_approve_selected` — approves selected transactions
- `review_bulk_approve_group` — approves all transactions in a group (same description)

### 12.5 GST Handling

- `set_gst_treatment` — sets GST treatment for a transaction
- `bulk_gst` — bulk sets GST treatment for multiple transactions
- `undo_bulk_gst` — reverses bulk GST changes
- `set_creditable_pct` — sets creditable percentage for apportioned transactions
- `set_gst_override` — manually overrides calculated GST amount
- `detect_apportionment` — AI-detects if a transaction requires apportionment
- `save_entity_gst_setting` — saves entity-level GST settings

### 12.6 Transaction Splitting

- `split_transaction` — splits a transaction into two or more parts
- `unsplit_transaction` — reverses a split

### 12.7 Classification Rules

- `create_rule` / `update_rule` / `delete_rule` / `toggle_rule` — manage classification rules
- `list_rules` — lists all rules for an entity

### 12.8 Opening Balance Validation

- `review_validate_opening_balance` — checks if the opening balance matches the prior period closing balance
- `review_post_opening_balance` — posts the opening balance adjustment to the TB

### 12.9 Bank Account Mapping

- `review_bank_account_mapping` — maps bank accounts (BSB + account number) to TB account codes
- `recalculate_bank_contra_entries` — recalculates all bank contra entries after mapping changes

### 12.10 TB Posting

When a transaction is approved (`is_confirmed=True`), `_post_confirmed_txn_to_tb()` creates:
1. An expense/income `TrialBalanceLine` for the transaction amount (net of GST)
2. A GST `TrialBalanceLine` for the GST component (if applicable)
3. A bank contra `TrialBalanceLine` (credit to bank account)

---

## 13. Tax Planning Module

**Views:** `core/views_tax_planning.py`  
**URL prefix:** `years/<pk>/tax-planning/`

### 13.1 Features

- **Calculate** (`tax_planning_calculate`) — calculates estimated tax payable from TB data
- **Save** (`tax_planning_save`) — saves the worksheet
- **Save Notes** (`tax_planning_save_notes`) — saves planning notes
- **Scenarios** — create, save, delete, apply named "what-if" scenarios
- **Finalise** (`tax_planning_finalise`) — locks the worksheet
- **Reopen** (`tax_planning_reopen`) — unlocks the worksheet

### 13.2 Trust Tax Planning

For trust entities, the tax planning worksheet includes per-beneficiary rows (`TaxPlanningBeneficiaryRow`) with:
- Beneficiary name and type
- Distribution amount
- Marginal tax rate
- Tax payable on distribution
- Franking credits

### 13.3 Documents

- `generate_trust_election` — generates a Family Trust Election document
- `generate_tax_planning_summary` — generates a tax planning summary Word document

---

## 14. Trust Distribution Workspace

**Views:** `core/views_trust.py`  
**URL prefix:** `years/<pk>/`

### 14.1 Six-Stage Workflow

The trust distribution workspace guides accountants through a structured 6-stage process:

| Stage | Name | Description |
| :--- | :--- | :--- |
| 1 | Income Calculation | Calculate trust net distributable income (NDI) by stream |
| 2 | Beneficiary Profiling | Profile each beneficiary's tax position |
| 3 | Distribution Modelling | Model up to 3 distribution scenarios |
| 4 | Section 100A Review | Assess Section 100A risk for each scenario |
| 5 | Trust Elections | Record FTE/IEE elections |
| 6 | Resolution Preparation | Prepare trustee resolution |

### 14.2 API Endpoints

All trust workspace endpoints return JSON:
- `trust_workspace_api` — get/update workspace
- `trust_stage_update` — update stage status
- `beneficiary_profiles_api` — CRUD for beneficiary profiles
- `distribution_scenarios_api` — CRUD for scenarios
- `confirm_scenario` / `delete_scenario` — scenario management
- `section_100a_api` — Section 100A assessment
- `trust_elections_api` / `confirm_election` — trust election records
- `trust_eva_context` — get Eva context for trust planning chat

### 14.3 Eva Integration

`trust_eva_context` provides Eva with the full trust workspace context for trust planning conversations (Section 8.11).

---

## 15. Division 7A Module

**Views:** `core/views_div7a.py`  
**URL prefix:** `years/<pk>/div7a/`

### 15.1 Dashboard

`div7a_dashboard` — shows the Div 7A assessment results for a financial year, including:
- Detected loan accounts with balances
- Compliance status of each loan
- Risk flags triggered
- Linked compliance records

### 15.2 Assessment

`div7a_run_assessment` — triggers the Div 7A assessment engine (`eva_div7a.py`).  
`div7a_assessment_api` — returns assessment results as JSON.

### 15.3 Compliance Records

`div7a_compliance_list` / `div7a_compliance_create` / `div7a_compliance_edit` — manage `Div7ACompliance` records (one per loan arrangement), tracking:
- Borrower name and entity
- Loan amount and start date
- Loan term (7 or 25 years)
- Whether secured
- Linked loan agreement document
- Compliance status

---

## 16. Office Admin Module

**Views:** `core/views_office_admin.py`  
**URL prefix:** `office/`

### 16.1 Dashboard

Daily task checklist for office admin staff. Tasks can be toggled complete/incomplete.

### 16.2 Correspondence Tracking

Tracks incoming/outgoing correspondence:
- `correspondence_list` — all correspondence
- `correspondence_incoming` / `outgoing` / `awaiting` / `documents_in` — filtered views
- `correspondence_create` — create new correspondence record
- `correspondence_update_status` — update status

### 16.3 ASIC Returns Tracker

`asic_returns` — tracks ASIC annual return lodgement status for all companies.

### 16.4 NOA Tracker

`noa_tracker` — tracks Notice of Assessment records.

### 16.5 Burning List

`burning_list` — urgent items requiring immediate attention.

### 16.6 Company Register

`company_register` — lists all company entities with key details.

### 16.7 Legal Documents Hub

`legal_documents` / `legal_doc_all` — firm-wide view of all legal documents across all entities.

### 16.8 Aged Receivables

`aged_receivables` / `statements_sent` / `debtors_overdue` — debtor management views.

### 16.9 Payment Plans

`payment_plans` — tracks ATO payment plans.

---

## 17. Third-Party Integrations

### 17.1 Xero (Accounting Software)

**Connection:** OAuth 2.0, per-entity or global  
**Models:** `AccountingConnection`, `XeroGlobalConnection`, `XeroTenant`  
**Features:**
- Import trial balance data for any period
- Global connection covers all tenants (firm-wide)
- Rapid import mode for batch processing
- Token auto-refresh

### 17.2 QuickBooks Online

**Connection:** OAuth 2.0, global  
**Models:** `QBGlobalConnection`, `QBTenant`  
**Features:** Same as Xero — import trial balance data

### 17.3 Xero Practice Manager (XPM)

**Connection:** OAuth 2.0  
**Models:** `XPMConnection`, `XPMSyncLog`  
**Features:**
- Sync client data (names, addresses, ABNs, contacts, relationships, notes) from XPM into StatementHub
- Creates/updates `Client`, `Entity`, `EntityOfficer` records
- Full sync via `run_full_sync()` in `xpm_sync.py`
- Manual trigger via `xpm_sync_now`

### 17.4 FuseSign (E-Signing)

**API:** REST API (`FUSESIGN_API_URL`, `FUSESIGN_API_KEY`)  
**Feature:** Send generated legal/compliance documents for electronic signing  
**Flow:** `legal_doc_send_fusesign` → creates FuseSign envelope → stores `fusesign_envelope_id` and `fusesign_status` on `LegalDocument`

### 17.5 Microsoft SharePoint (Knowledge Brain)

**Connection:** Microsoft Graph API (client credentials flow)  
**Config:** `SHAREPOINT_TENANT_ID`, `SHAREPOINT_CLIENT_ID`, `SHAREPOINT_CLIENT_SECRET`, `SHAREPOINT_SITE_ID`, `SHAREPOINT_DRIVE_ID`  
**Feature:** Source of truth for Eva's Knowledge Brain documents (see Section 8.3)

### 17.6 AWS Textract (OCR)

**Config:** `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `AWS_TEXTRACT_SNS_TOPIC_ARN`, `AWS_TEXTRACT_ROLE_ARN`  
**Feature:** OCR for scanned governing documents (trust deeds, constitutions) when native text extraction fails  
**Flow:** `governing_doc_upload` → `governing_doc_extract` → async Textract job → `process_textract_result` Celery task

### 17.7 Airtable (Bank Statement Workflow)

**Config:** `AIRTABLE_API_KEY`, `AIRTABLE_BASE_ID`, `AIRTABLE_PENDING_TABLE`, `AIRTABLE_JOBS_TABLE`, `AIRTABLE_LEARNING_TABLE`  
**Feature:** Optional integration — bank statements can be submitted via Airtable and synced into the review workflow  
**Status:** Optional (gracefully disabled if `AIRTABLE_API_KEY` not set)

### 17.8 Anthropic / OpenAI

**Config:** `ANTHROPIC_API_KEY` (Anthropic), `OPENAI_API_KEY` (OpenAI-compatible)  
**Feature:** LLM provider for all Eva AI features  
**Switch:** `USE_ANTHROPIC` environment variable

---

## 18. Authentication & Security

### 18.1 Authentication Flow

1. User navigates to `/accounts/login/`
2. `MCSLoginView` (extends `auth_views.LoginView`) handles username/password
3. On success, if `totp_confirmed=True`, redirects to TOTP verify (`totp_verify_view`)
4. User enters 6-digit TOTP code (verified via `pyotp`)
5. On TOTP success, session is marked as 2FA-verified
6. `Require2FAMiddleware` enforces 2FA on all protected views

### 18.2 2FA Setup

- `setup_2fa_view` — generates TOTP secret, displays QR code, user scans with authenticator app
- `user_reset_2fa` — admin resets another user's 2FA (admin only)

### 18.3 Invitation-Based Onboarding

- Admins create invitations (`invitation_create`) — sends email with unique token
- New user clicks link → `invitation_signup_view` — sets password and completes 2FA setup
- Invitation expires after a configurable period

### 18.4 Password Management

- `send_password_reset` — admin sends password reset email to a user
- `send_all_password_resets` — bulk send to all users
- `password_reset_confirm_view` / `password_reset_complete_view` — standard Django password reset flow

### 18.5 Security Middleware & Settings

- **`Require2FAMiddleware`** — redirects to TOTP verify if session not 2FA-verified
- **`CSPMiddleware`** — Content Security Policy headers (django-csp)
- **`SecurityMiddleware`** — Django security headers
- **`WhiteNoiseMiddleware`** — static file serving
- **`django-ratelimit`** — rate limiting on login and sensitive endpoints
- **TFN encryption** — Tax File Numbers encrypted at rest using `cryptography` Fernet
- **TOTP secret encryption** — TOTP secrets encrypted using `EncryptedCharField`
- **SSL** — enforced in production (`DB_SSLMODE=prefer`)

---

## 19. Background Tasks (Celery)

**Broker:** Redis (`CELERY_BROKER_URL`)  
**Result backend:** Redis (`CELERY_RESULT_BACKEND`)  
**Timezone:** `Australia/Melbourne`  
**Scheduler:** `django_celery_beat` (database-backed)

### 19.1 Scheduled Tasks

| Task | Schedule | Description |
| :--- | :--- | :--- |
| `core.sync_knowledge_brain` | Every 2 hours | Sync SharePoint → Knowledge Brain |

### 19.2 On-Demand Tasks

| Task Name | Trigger | Description |
| :--- | :--- | :--- |
| `core.sync_knowledge_brain` | Manual trigger or schedule | SharePoint sync, chunk, embed |
| `core.eva_chat_response` | Eva chat message | Build context, RAG search, call LLM |
| `core.eva_finalisation_review` | Submit for Eva review | 14 compliance checks, create findings |
| `core.eva_client_summary` | FY locked | Generate bullet + narrative summaries |
| `core.extract_governing_document` | Governing doc upload | Native text → Textract if scanned |
| `core.process_textract_result` | Textract SNS callback | Assemble OCR text, store confidence |
| `core.generate_legal_document` | Legal doc generation | docxtpl render, LibreOffice PDF |
| `core.assemble_client_package` | Package assembly | Combine all PDFs, cover letter |
| `core.bulk_package_generation` | Bulk package trigger | Iterate entities, check readiness |
| `core.eva_bas_commentary` | Commentary generate | BAS period commentary generation |
| `core.div7a_assessment` | Div 7A run | 8-rule Div 7A detection per FY |
| `core.div7a_batch_assessment` | Batch trigger | Div 7A across multiple entities |
| `core.eva_proactive_suggestion` | Event trigger | Proactive suggestion generation |

---

## 20. URL & Feature Inventory

### 20.1 `accounts` App URLs

| URL Name | Description |
| :--- | :--- |
| `login` | Login page |
| `logout` | Logout |
| `totp_verify` | TOTP 2FA verification |
| `invitation_signup` | Accept invitation and create account |
| `profile` | User profile |
| `user_list` | List all users (admin) |
| `user_create` | Create user (admin) |
| `user_edit` | Edit user (admin) |
| `user_reset_2fa` | Reset user's 2FA (admin) |
| `send_password_reset` | Send password reset email |
| `send_all_password_resets` | Bulk password reset |
| `password_reset_confirm` | Password reset confirmation |
| `password_reset_complete` | Password reset complete |
| `invitation_list` | List invitations |
| `invitation_create` | Create invitation |
| `invitation_resend` | Resend invitation email |
| `invitation_revoke` | Revoke invitation |
| `setup_2fa` | 2FA setup wizard |

### 20.2 `core` App URLs — Entity & Financial Year Management

| URL Name | Description |
| :--- | :--- |
| `dashboard` | Main dashboard |
| `client_list` | Client/entity list |
| `client_create` | Create client |
| `client_detail` | Client detail |
| `client_edit` | Edit client |
| `entity_create` | Create entity |
| `entity_detail` | Entity detail |
| `entity_edit` | Edit entity |
| `financial_year_create` | Create financial year |
| `financial_year_detail` | Financial year detail (main workspace) |
| `financial_year_status` | Change FY status |
| `roll_forward` | Roll forward to new year |
| `reopen_financial_year` | Reopen locked year |
| `reroll_forward` | Re-roll forward |
| `delete_unfinalised_fy` | Delete draft FY |
| `entity_officers` | Manage entity officers |
| `entity_officer_create/edit/delete` | Officer CRUD |
| `associate_create/edit/delete` | Associate CRUD |
| `entity_link_search/create/delete` | Entity relationship management |
| `software_create/edit/delete` | Accounting software records |
| `meeting_note_create/edit/detail/delete/toggle_followup` | Meeting notes |
| `entity_bulk_action` | Bulk entity operations |
| `entity_assignments` | Entity assignment management |
| `bulk_assign_entities` | Bulk assign entities to accountants |
| `update_entity_assignment` | Update single entity assignment |
| `xrm_search` / `xrm_pull` | XPM client data pull |

### 20.3 `core` App URLs — Trial Balance & Journals

| URL Name | Description |
| :--- | :--- |
| `trial_balance_import` | Upload trial balance file |
| `review_tb_import` | Preview TB import |
| `commit_tb_import` | Commit TB import |
| `trial_balance_view` | View trial balance |
| `trial_balance_pdf` | Download TB as PDF |
| `trial_balance_download` | Download TB as Word/Excel |
| `trial_balance_template_download` | Download TB import template |
| `account_code_breakdown` | Drill down on account code |
| `tb_line_reallocate` | Reallocate TB line to different account |
| `account_mapping_list` | List account mappings |
| `account_mapping_create` | Create account mapping |
| `map_client_accounts` | Map client accounts to standard CoA |
| `htmx_map_tb_line` | HTMX: map single TB line |
| `htmx_update_tb_mapping` | HTMX: update TB mapping |
| `adjustment_list` | List adjusting journals |
| `adjustment_create` | Create adjusting journal |
| `journal_detail` | Journal detail |
| `journal_post` | Post journal to TB |
| `journal_delete` | Delete journal |
| `journals_pdf` | Download all journals as PDF |
| `journal_upload` | Upload bulk journals (Excel) |
| `review_journal_upload` | Preview journal upload |
| `commit_journal_upload` | Commit journal upload |
| `journal_template_download` | Download journal template |
| `bulk_journal_detail/delete/reallocate/line_delete` | Bulk journal management |
| `account_list_api` | API: list accounts for journal form |
| `net_profit_api` | API: get net profit figure |

### 20.4 `core` App URLs — Chart of Accounts

| URL Name | Description |
| :--- | :--- |
| `chart_of_accounts` | Master chart of accounts |
| `coa_add/edit/delete` | Master CoA CRUD |
| `coa_check_code` / `coa_suggest_code` | Code validation/suggestion |
| `chart_of_accounts_api` | API: search master CoA |
| `coa_search_api` | API: search CoA |
| `coa_propagate_tax_codes` | Propagate tax codes across entities |
| `entity_coa_add/edit/delete` | Entity-specific CoA CRUD |
| `entity_coa_suggest_code/check_code` | Entity CoA code tools |
| `entity_coa_search_api` | API: search entity CoA |

### 20.5 `core` App URLs — Financial Statements & Documents

| URL Name | Description |
| :--- | :--- |
| `financial_statements_view` | View financial statements |
| `line_item_breakdown` | Drill down on FS line item |
| `generate_document` | Generate financial statements (docx/pdf) |
| `generate_management_accounts` | Generate management accounts |
| `generate_distribution_minutes` | Generate distribution minutes |
| `delete_document` | Delete generated document |
| `populate_comparatives` | Populate comparative figures |
| `override_comparative` | Override comparative figure |
| `lock_comparatives` | Lock comparative figures |
| `regenerate_document` | Regenerate a document |
| `bulk_regenerate` | Bulk regenerate documents |
| `mark_document_final` | Mark document as final |

### 20.6 `core` App URLs — Risk Engine & Audit

| URL Name | Description |
| :--- | :--- |
| `audit_library` | Audit library / risk rules list |
| `risk_badge_api` | API: get risk badge count |
| `risk_flags` | View risk flags for FY |
| `resolve_risk_flag` | Resolve a risk flag |
| `run_risk_engine` | Run the risk engine |
| `ai_analyse_flag` | AI analyse a single flag |
| `ai_analyse_all_flags` | AI analyse all flags |
| `ai_prioritise_flags` | AI prioritise flags |
| `generate_risk_report` | Generate risk report document |
| `ai_feedback` | Record AI feedback |

### 20.7 `core` App URLs — Eva AI

| URL Name | Description |
| :--- | :--- |
| `eva_chat_api` | Eva chat (GET: history, POST: send message) |
| `ask_eva_review` | Trigger Eva finalisation review |
| `eva_review_detail` | View Eva review results |
| `eva_review_status` | Poll Eva review status |
| `eva_resolve_finding` | Resolve an Eva finding |
| `eva_preflight` | Run pre-flight checks |
| `eva_rerun_review` | Re-run Eva review |
| `eva_finalise` | Finalise (lock) after Eva review |
| `knowledge_sync` | Trigger Knowledge Brain sync |
| `knowledge_documents` | List knowledge documents |
| `knowledge_search` | Search knowledge brain |
| `knowledge_status` | Knowledge brain status |
| `knowledge_brain_admin` | Knowledge brain admin |
| `trigger_knowledge_sync` | Manual sync trigger |

### 20.8 `core` App URLs — BAS / GST

| URL Name | Description |
| :--- | :--- |
| `gst_activity_statement` | BAS dashboard |
| `gst_activity_statement_download` | Download BAS (Excel/PDF) |
| `bas_lodge_period` | Lodge a BAS period |
| `bas_unlodge_period` | Unlodge a BAS period |
| `bas_coverage_check` | Check transaction coverage |
| `bas_reallocate_transaction` | Reallocate transaction GST code |
| `bas_bulk_reallocate` | Bulk reallocate |
| `bas_entity_accounts_json` | API: entity accounts for reallocation |
| `bas_commentary_generate` | Generate AI commentary |
| `bas_commentary_list` | List commentaries |
| `bas_commentary_detail` | View commentary |
| `bas_commentary_update` | Update commentary |
| `bas_commentary_regenerate` | Regenerate commentary |
| `bas_commentary_download` | Download commentary as Word |
| `bas_commentary_status` | Poll commentary status |
| `bas_commentary_mark_sent` | Mark commentary as sent |
| `bas_commentary_delete` | Delete commentary |
| `bas_commentary_compare` | Compare two commentary versions |

### 20.9 `core` App URLs — Depreciation & Stock

| URL Name | Description |
| :--- | :--- |
| `depreciation_add/edit/delete` | Depreciation asset CRUD |
| `depreciation_roll_forward` | Roll forward depreciation schedule |
| `depreciation_post_to_tb` | Post depreciation to trial balance |
| `depreciation_add_from_transaction` | Add asset from bank transaction |
| `stock_add/edit/delete` | Stock item CRUD |
| `stock_push_to_tb` | Push stock adjustment to TB |

### 20.10 `core` App URLs — Bank Statement Review (in-core)

| URL Name | Description |
| :--- | :--- |
| `review_push_to_tb` | Push all approved transactions to TB |
| `review_approve_transaction` | Approve single transaction |
| `review_unconfirm_transaction` | Unconfirm transaction |
| `review_approve_all` | Approve all transactions |
| `review_classify_ai` | Trigger AI classification |
| `review_classify_status` | Poll AI classification status |
| `review_bulk_approve_group` | Approve all in group |
| `review_approve_selected` | Approve selected |
| `review_export_pdf` | Export transactions as PDF |
| `bank_statement_template_download` | Download bank statement template |
| `review_bank_account_mapping` | Bank account mapping |
| `recalculate_bank_contra_entries` | Recalculate contra entries |
| `review_validate_opening_balance` | Validate opening balance |
| `review_post_opening_balance` | Post opening balance |
| `review_bulk_edit_transactions` | Bulk edit transactions |
| `review_delete_transaction` | Delete single transaction |
| `review_delete_all_transactions` | Delete all transactions |
| `review_delete_selected_transactions` | Delete selected transactions |

### 20.11 `core` App URLs — Trust Distribution

| URL Name | Description |
| :--- | :--- |
| `trust_distribution` | Trust distribution workspace |
| `generate_beneficiary_statement` | Generate beneficiary statement |
| `trust_workspace_api` | Trust workspace API |
| `trust_stage_update` | Update stage status |
| `beneficiary_profiles_api` | Beneficiary profiles API |
| `distribution_scenarios_api` | Distribution scenarios API |
| `confirm_scenario` / `delete_scenario` | Scenario management |
| `section_100a_api` | Section 100A assessment API |
| `trust_elections_api` / `confirm_election` | Trust elections API |
| `trust_eva_context` | Eva context for trust planning |

### 20.12 `core` App URLs — Partnership

| URL Name | Description |
| :--- | :--- |
| `partnership_allocation` | Partnership allocation workspace |
| `generate_partner_statement` | Generate partner statement |
| `partner_statements` | Partner statements list |
| `generate_partner_statements` | Generate all partner statements |
| `generate_partnership_tax_summary` | Generate partnership tax summary |

### 20.13 `core` App URLs — Tax Planning

| URL Name | Description |
| :--- | :--- |
| `tax_planning_tab` | Tax planning tab |
| `tax_planning_calculate` | Calculate tax |
| `tax_planning_save` | Save worksheet |
| `tax_planning_save_notes` | Save notes |
| `tax_planning_scenario_save/delete/apply` | Scenario management |
| `tax_planning_finalise` / `tax_planning_reopen` | Finalise/reopen |
| `generate_trust_election` | Generate FTE document |
| `generate_tax_planning_summary` | Generate summary document |

### 20.14 `core` App URLs — Compliance Documents

| URL Name | Description |
| :--- | :--- |
| `dividend_wizard` | Dividend declaration wizard |
| `dividend_create` | Create dividend event |
| `dividend_detail` | Dividend detail |
| `generate_solvency_resolution` | Generate solvency resolution |
| `generate_directors_declaration` | Generate director's declaration |
| `directors_report_wizard` | Director's report wizard |
| `directors_report_draft_eva` | Eva-assisted director's report |
| `generate_loan_acknowledgment` | Generate shareholder loan acknowledgment |
| `generate_management_rep_letter` | Generate management rep letter |
| `generate_cover_letter` | Generate cover letter |
| `engagement_letter_wizard` | Engagement letter wizard |
| `engagement_letter_generate` | Generate engagement letter |

### 20.15 `core` App URLs — Legal Documents

| URL Name | Description |
| :--- | :--- |
| `legal_template_list` | List legal document templates |
| `legal_template_upload` | Upload Word template |
| `legal_doc_wizard` | Legal document wizard (generic) |
| `legal_doc_generate` | Generate legal document |
| `legal_doc_list` | List legal documents for entity |
| `legal_doc_download` | Download legal document |
| `legal_doc_send_fusesign` | Send for e-signing |
| `legal_doc_entity_search` | Search entities for legal doc |

### 20.16 `core` App URLs — Division 7A

| URL Name | Description |
| :--- | :--- |
| `div7a_dashboard` | Div 7A dashboard |
| `div7a_run_assessment` | Run Div 7A assessment |
| `div7a_assessment_api` | Assessment results API |
| `div7a_compliance_create/list/edit` | Compliance record management |

### 20.17 `core` App URLs — Client Summary & Package

| URL Name | Description |
| :--- | :--- |
| `client_summary_view` | View client summary |
| `client_summary_api` | Client summary API |
| `client_summary_generate` | Generate client summary |
| `package_assembly` | Package assembly view |
| `package_assemble` | Assemble package |
| `package_send_for_signing` | Send package for signing |
| `bulk_generate_packages` | Bulk generate packages |
| `bulk_readiness_check` | Check package readiness |

### 20.18 `core` App URLs — Workpaper Notes

| URL Name | Description |
| :--- | :--- |
| `workpaper_notes_api` | Workpaper notes API |
| `carry_forward_notes` | Carry forward notes to new year |
| `export_workpaper_notes` | Export notes as document |

### 20.19 `core` App URLs — Templates

| URL Name | Description |
| :--- | :--- |
| `template_list` | List FS templates |
| `template_create` | Create template |
| `template_edit` | Edit template |
| `template_preview` | Preview template |
| `template_new_version` | Create new template version |
| `template_delete` | Delete template |
| `template_toggle_active` | Activate/deactivate template |
| `template_update_structure` | Update template structure |

### 20.20 `core` App URLs — Bulk Import

| URL Name | Description |
| :--- | :--- |
| `bulk_import_start` | Start bulk entity import |
| `bulk_import_template` | Download bulk import template |
| `bulk_import_map` | Map columns |
| `bulk_import_validate` | Validate import data |
| `bulk_import_execute` | Execute import |
| `entity_import_handiledger` | HandiLedger import |
| `access_ledger_import` | Access Ledger import |

### 20.21 `core` App URLs — Notifications & HTMX

| URL Name | Description |
| :--- | :--- |
| `notifications_api` | Get notifications |
| `mark_notification_read` | Mark notification read |
| `mark_all_notifications_read` | Mark all read |
| `htmx_client_search` | HTMX entity search |
| `htmx_map_tb_line` | HTMX map TB line |
| `htmx_update_tb_mapping` | HTMX update mapping |
| `coa_search_api` | CoA search API |
| `entity_coa_search_api` | Entity CoA search API |

### 20.22 `office_admin` App URLs

| URL Name | Description |
| :--- | :--- |
| `dashboard` | Office admin dashboard |
| `toggle_task` | Toggle daily task |
| `correspondence_list/incoming/outgoing/awaiting/documents_in` | Correspondence views |
| `correspondence_create` | Create correspondence |
| `correspondence_update_status` | Update status |
| `noa_tracker` | NOA tracker |
| `asic_returns` | ASIC returns tracker |
| `burning_list` | Burning list |
| `company_register` | Company register |
| `legal_documents` / `legal_doc_all` | Legal documents hub |
| `legal_doc_select_entity` | Select entity for legal doc |
| `legal_doc_redirect_wizard` | Redirect to wizard |
| `legal_doc_entity_search_api` | Entity search API |
| `aged_receivables` / `statements_sent` / `debtors_overdue` | Debtor management |
| `payment_plans` | Payment plans |

### 20.23 `review` App URLs

| URL Name | Description |
| :--- | :--- |
| `dashboard` | Review dashboard |
| `review_detail` | Review job detail |
| `confirm_transaction` | Confirm transaction |
| `submit_review` | Submit completed review |
| `accept_all` | Accept all AI suggestions |
| `upload_statement` | Upload bank statement |
| `parse_statement` | Parse uploaded statement |
| `upload_preview` | Preview parsed transactions |
| `confirm_import` | Confirm import |
| `classify_batch` | Classify batch of transactions |
| `bulk_approve_group` | Bulk approve group |
| `export_pdf` | Export transactions as PDF |
| `notify_new_job` | Notify of new review job |
| `search_transactions` | Search transactions |
| `split_transaction` | Split transaction |
| `unsplit_transaction` | Unsplit transaction |
| `create/update/delete/toggle/list_rules` | Classification rule management |
| `set_gst_treatment` / `bulk_gst` / `undo_bulk_gst` | GST treatment |
| `set_creditable_pct` | Creditable percentage |
| `set_gst_override` | GST override |
| `detect_apportionment` | Detect apportionment |
| `save_entity_gst_setting` | Save GST settings |

### 20.24 `integrations` App URLs

| URL Name | Description |
| :--- | :--- |
| `connections_hub` | Connections dashboard |
| `connection_manage` | Manage entity connection |
| `oauth_connect` | Start OAuth flow |
| `oauth_callback` | OAuth callback |
| `select_tenant` | Select Xero/QB tenant |
| `disconnect` | Disconnect integration |
| `import_from_cloud` | Import TB from cloud |
| `select_provider_import` | Select import provider |
| `xero_select_tenant_import` | Select Xero tenant for import |
| `qb_select_tenant_import` | Select QB tenant for import |
| `review_import` | Review cloud import |
| `commit_import` | Commit cloud import |
| `quick_add_entity_account` | Quick add entity account |
| `xero_global_dashboard/connect/callback/refresh_tenants/stop_rapid/disconnect` | Global Xero management |
| `qb_global_dashboard/connect/callback/stop_rapid/disconnect` | Global QB management |
| `xpm_dashboard/connect/callback/select_tenant/disconnect/sync_now` | XPM management |

---

## 21. Key Design Patterns & Gotchas

### 21.1 The Immutable Trial Balance

**This is the most critical design invariant.** Original trial balance imports are never modified. All adjustments — whether from manual journals, bank statement review, depreciation, stock, or bulk uploads — create new `TrialBalanceLine` records with `is_adjustment=True`. The `source` field tracks where each line came from.

When displaying the trial balance, the application aggregates all lines (original + adjustments) by account code to produce netted balances.

**Implication:** Never write code that updates an existing `TrialBalanceLine.debit` or `TrialBalanceLine.credit` field. Always create a new line.

### 21.2 Bank Contra Entries

Every approved bank statement transaction creates **three** trial balance lines:
1. The expense/income line (net amount)
2. The GST line (if applicable)
3. A bank contra line (`is_contra=True`) — credit to the bank account

The bank contra ensures the trial balance remains balanced. `recalculate_bank_contra_entries` recalculates all contra entries if bank account mappings change.

### 21.3 Three-Tier Account Mapping Lookup

When resolving an account code to a financial statement line:
1. Check `ClientAccountMapping` (entity-specific override) — highest priority
2. Check `TrialBalanceLine.mapped_line_item` (set during import or manual mapping)
3. Check `ChartOfAccount` (master CoA) — lowest priority

### 21.4 Large Files

- `core/views.py` — 11,098 lines. Use `grep -n` to find functions.
- `core
/models.py` — 4,873 lines. Use `grep -n "^class "` to find models.
- `review/views.py` — 2,636 lines.
- `integrations/views.py` — 1,699 lines.
- Most frontend JavaScript is **inline** in Django templates under `/templates/core/`.

### 21.5 HTMX Patterns

The platform uses HTMX for partial page updates. Key patterns:
- `hx-post` with `hx-target` for form submissions that update a section
- `hx-get` for loading dynamic content (e.g. account picker, TB line mapping)
- `hx-swap="outerHTML"` for replacing elements in place
- `HtmxMiddleware` processes `HX-Request` headers

### 21.6 Celery Task Dispatch Pattern

All long-running operations follow this pattern:
1. View receives POST request
2. View dispatches Celery task: `task_name.delay(pk, user_pk)`
3. View returns JSON `{"status": "pending", "task_id": "..."}`
4. Frontend polls a status endpoint (`*_status` URL) every 2 seconds
5. Status endpoint returns `{"status": "running"|"completed"|"failed", ...}`
6. On completion, frontend redirects or updates the UI

### 21.7 Document Generation Pattern

All document generation follows this pattern:
1. Build context dictionary from DB models
2. Load template (either `LegalDocumentTemplate` file or hardcoded `docgen.py` logic)
3. Render using `docxtpl.DocxTemplate` (for legal docs) or `python-docx` (for financial statements)
4. Optionally convert to PDF via LibreOffice headless: `subprocess.run(['libreoffice', '--headless', '--convert-to', 'pdf', ...])`
5. Save file to `GeneratedDocument` or `LegalDocument` record

### 21.8 AI Service Abstraction (`ai_service.py`)

All LLM calls go through `ai_service.py`. Key functions:
- `call_llm(prompt, tier, system_prompt)` — synchronous call
- `call_llm_stream(prompt, tier, system_prompt)` — streaming generator
- `analyse_risk_flag(flag)` — risk flag analysis
- `batch_analyse_flags(flags)` — batch analysis
- `prioritise_flags(flags)` — flag prioritisation
- `generate_risk_summary_report(fy)` — risk report

The `tier` parameter (`haiku`, `sonnet`, `opus`) selects the appropriate model.

### 21.9 Encryption

Two fields use encryption:
- `Entity.tfn` — Tax File Number (Fernet symmetric encryption)
- `User.totp_secret` — TOTP secret (Fernet symmetric encryption)

The encryption key is stored in the `FIELD_ENCRYPTION_KEY` environment variable. **If this key is lost, all encrypted data is unrecoverable.**

### 21.10 Financial Year Status Guard

Many views check `financial_year.status` before allowing operations:
- Locked FYs cannot have journals posted, TB lines added, or status changed (except by admin reopen)
- `eva_cleared` status is required before `eva_finalise` can be called
- `pending_eva` status is set when Eva review is triggered

### 21.11 Deduplication Hash for Risk Flags

`RiskFlag.flag_hash` is a hash of `(financial_year_id, rule_id, description)`. Before creating a new flag, the engine checks if an identical hash already exists. This prevents duplicate flags when the engine is re-run.

### 21.12 Learned Account Mappings

The system learns account code → AccountMapping associations per entity. After each import or manual mapping, the association is stored in `ClientAccountMapping`. On subsequent imports, `_apply_tb_learned_mappings()` auto-applies these learned mappings, reducing manual mapping work over time.

---

## 22. Management Commands

| Command | Description |
| :--- | :--- |
| `seed_risk_rules` | Seeds all 80+ risk rules into the database |
| `seed_account_mappings` | Seeds the master chart of accounts |
| `import_access_ledger` | Bulk imports from Access Ledger export file |
| `import_chart_of_accounts` | Imports a chart of accounts from CSV/Excel |
| `sync_knowledge_brain` | Manually triggers SharePoint → Knowledge Brain sync |
| `map_accounts` | Runs account mapping for all unmapped TB lines |
| `remap_trial_balances` | Re-runs mapping for all trial balances |
| `scrape_ato_updates` | Scrapes ATO website for benchmark/rate updates |
| `clear_test_data` | (review app) Clears test review data |

---

## 23. Environment Variables Reference

| Variable | Required | Description |
| :--- | :--- | :--- |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SECRET_KEY` | Yes | Django secret key |
| `DEBUG` | No | `True` for development |
| `ALLOWED_HOSTS` | Yes | Comma-separated allowed hosts |
| `FIELD_ENCRYPTION_KEY` | Yes | Fernet key for TFN/TOTP encryption |
| `ANTHROPIC_API_KEY` | Yes* | Anthropic API key |
| `OPENAI_API_KEY` | Yes* | OpenAI API key (for embeddings + fallback LLM) |
| `USE_ANTHROPIC` | No | `true` to use Anthropic, else OpenAI-compatible |
| `CELERY_BROKER_URL` | Yes | Redis URL for Celery broker |
| `CELERY_RESULT_BACKEND` | Yes | Redis URL for Celery results |
| `SHAREPOINT_TENANT_ID` | No | Microsoft tenant ID (Knowledge Brain) |
| `SHAREPOINT_CLIENT_ID` | No | Azure app client ID |
| `SHAREPOINT_CLIENT_SECRET` | No | Azure app client secret |
| `SHAREPOINT_SITE_ID` | No | SharePoint site ID |
| `SHAREPOINT_DRIVE_ID` | No | SharePoint drive ID |
| `XERO_CLIENT_ID` | No | Xero OAuth client ID |
| `XERO_CLIENT_SECRET` | No | Xero OAuth client secret |
| `XPM_SYNC_ENABLED` | No | `true` to enable XPM sync |
| `MYOB_CLIENT_ID` | No | MYOB client ID (legacy, removed) |
| `MYOB_CLIENT_SECRET` | No | MYOB client secret (legacy, removed) |
| `QBO_CLIENT_ID` | No | QuickBooks Online client ID |
| `QBO_CLIENT_SECRET` | No | QuickBooks Online client secret |
| `AIRTABLE_API_KEY` | No | Airtable API key |
| `AIRTABLE_BASE_ID` | No | Airtable base ID |
| `AIRTABLE_PENDING_TABLE` | No | Airtable pending transactions table |
| `AIRTABLE_JOBS_TABLE` | No | Airtable jobs table |
| `AIRTABLE_LEARNING_TABLE` | No | Airtable learning table |
| `FUSESIGN_API_KEY` | No | FuseSign API key |
| `FUSESIGN_API_URL` | No | FuseSign API URL |
| `AWS_ACCESS_KEY_ID` | No | AWS access key (Textract) |
| `AWS_SECRET_ACCESS_KEY` | No | AWS secret key (Textract) |
| `AWS_REGION` | No | AWS region (default: ap-southeast-2) |
| `AWS_TEXTRACT_SNS_TOPIC_ARN` | No | SNS topic for Textract callbacks |
| `AWS_TEXTRACT_ROLE_ARN` | No | IAM role for Textract |
| `DB_CONN_MAX_AGE` | No | DB connection max age (default: 600) |
| `DB_SSLMODE` | No | DB SSL mode (default: prefer) |

*At least one of `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` is required. `OPENAI_API_KEY` is always required for embeddings (Knowledge Brain).

---

## 24. Known Architectural Observations for Code Review

The following observations are intended to guide a thorough code review. They are not confirmed bugs but areas warranting careful inspection.

### 24.1 Views.py Size & Complexity

`core/views.py` at 11,098 lines is extremely large and contains a mix of concerns. Functions performing TB calculations, document generation, AI orchestration, and HTTP response handling are intermixed. This creates high coupling and makes testing difficult. The partial refactoring into `views_*.py` files has begun but is incomplete.

### 24.2 Inline JavaScript in Templates

All frontend JavaScript is written inline within Django templates. This means JavaScript logic is scattered across 100+ template files with no bundling, minification, or linting. HTMX event handlers, Eva streaming logic, and the dynamic journal form are all inline.

### 24.3 Celery Task Error Handling

Celery tasks use `max_retries=3` but the retry logic varies. Some tasks use `self.retry(exc=e)` correctly; others catch exceptions and update status to `failed` without retrying. Consistency should be verified.

### 24.4 AI Response Parsing

LLM responses are parsed as JSON with a `_repair_truncated_json()` fallback. If the LLM returns malformed JSON that cannot be repaired, findings may be silently dropped. The error handling path should be verified to ensure `EvaReview.status` is correctly set to `eva_error` in all failure cases.

### 24.5 Encryption Key Rotation

There is no documented key rotation procedure for `FIELD_ENCRYPTION_KEY`. If the key needs to be rotated, all encrypted TFN and TOTP secret fields would need to be re-encrypted. This is a data management risk.

### 24.6 MYOB Removal

The MYOB integration has been removed but `MYOBGlobalConnection` and `MYOBCompanyFile` models remain. The `_myob_removed()` stub in views returns an error. Migrations for these models still exist. This is technical debt that should be documented.

### 24.7 Airtable Dependency

The bank statement review workflow has an optional Airtable dependency. The code gracefully handles missing `AIRTABLE_API_KEY` but the Airtable sync path (`_sync_from_airtable()`) should be verified for error handling when the Airtable API is unavailable.

### 24.8 LibreOffice Dependency

PDF generation from Word documents requires LibreOffice to be installed on the server. This is a system-level dependency not captured in `requirements.txt`. If LibreOffice is not installed, PDF generation will fail silently or with a subprocess error.

### 24.9 AWS Textract Async Pattern

The Textract integration uses an asynchronous pattern (start job → SNS callback → Celery task). The SNS webhook endpoint must be publicly accessible and correctly configured. If the SNS callback is not received, `GoverningDocument.extraction_method` will remain `textract` with no extracted text.

### 24.10 Comparative Period Logic

The `populate_comparatives` and `lock_comparatives` functions interact with the `comparative_locked` and `comparative_override` fields on `TrialBalanceLine`. The interaction between locked comparatives, manual overrides, and roll-forward operations should be carefully reviewed for edge cases.

### 24.11 Trust Distribution Workspace Concurrency

The trust distribution workspace uses a `OneToOneField` on `FinancialYear`. If two users attempt to update the workspace simultaneously, the last write wins. There is no optimistic locking or conflict detection.

### 24.12 Knowledge Brain Embedding Storage

Vector embeddings are stored as `JSONField` (list of floats) in `KnowledgeChunk`. This is a pragmatic choice that avoids a dedicated vector database, but cosine similarity search loads all chunks into memory for comparison. This will not scale well beyond ~10,000 chunks. Performance should be monitored.

### 24.13 FuseSign Webhook

The FuseSign integration sends documents for e-signing but there is no documented webhook endpoint for receiving signing completion callbacks. The `fusesign_status` field on `LegalDocument` may need to be manually updated, or a webhook endpoint needs to be verified.

### 24.14 Session-Based 2FA State

The 2FA verification state is stored in the Django session (`request.session['2fa_verified'] = True`). Session fixation attacks are mitigated by Django's session rotation on login, but this should be verified.

### 24.15 Rate Limiting Coverage

`django-ratelimit` is installed but the coverage of rate-limited endpoints should be audited. At minimum, the login view, TOTP verify view, and AI endpoints should be rate-limited.

---

*End of StatementHub Technical Specification*
