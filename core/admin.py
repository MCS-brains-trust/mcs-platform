from django.contrib import admin
from .models import (
    Client, Entity, FinancialYear, TrialBalanceLine,
    AccountMapping, ClientAccountMapping, NoteTemplate,
    AdjustingJournal, JournalLine, FinancialStatementTemplate,
    GeneratedDocument, AuditLog, EntityOfficer, DepreciationAsset,
    BankAccountMapping,
)


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "contact_email", "assigned_accountant", "is_active", "created_at")
    list_filter = ("is_active", "assigned_accountant")
    search_fields = ("name", "contact_email", "xpm_client_id")


@admin.register(Entity)
class EntityAdmin(admin.ModelAdmin):
    list_display = ("entity_name", "entity_type", "client", "abn", "reporting_framework")
    list_filter = ("entity_type", "reporting_framework", "company_size")
    search_fields = ("entity_name", "abn", "acn")


class TrialBalanceLineInline(admin.TabularInline):
    model = TrialBalanceLine
    extra = 0


@admin.register(FinancialYear)
class FinancialYearAdmin(admin.ModelAdmin):
    list_display = ("entity", "year_label", "start_date", "end_date", "status")
    list_filter = ("status",)
    search_fields = ("entity__entity_name", "year_label")
    inlines = [TrialBalanceLineInline]


@admin.register(AccountMapping)
class AccountMappingAdmin(admin.ModelAdmin):
    list_display = ("standard_code", "line_item_label", "financial_statement", "statement_section", "display_order")
    list_filter = ("financial_statement",)
    search_fields = ("standard_code", "line_item_label")


@admin.register(ClientAccountMapping)
class ClientAccountMappingAdmin(admin.ModelAdmin):
    list_display = ("entity", "client_account_code", "client_account_name", "mapped_line_item")
    list_filter = ("entity__entity_type",)
    search_fields = ("client_account_code", "client_account_name")


@admin.register(NoteTemplate)
class NoteTemplateAdmin(admin.ModelAdmin):
    list_display = ("note_number", "title", "trigger_type", "aasb_reference", "last_reviewed")
    list_filter = ("trigger_type",)


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 2


@admin.register(AdjustingJournal)
class AdjustingJournalAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "journal_date", "description", "created_by", "created_at")
    inlines = [JournalLineInline]


@admin.register(FinancialStatementTemplate)
class FinancialStatementTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "entity_type", "version", "is_active")
    list_filter = ("entity_type", "is_active")


@admin.register(GeneratedDocument)
class GeneratedDocumentAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "file_format", "generated_by", "generated_at")


@admin.register(EntityOfficer)
class EntityOfficerAdmin(admin.ModelAdmin):
    list_display = ("full_name", "role", "entity", "title", "is_signatory", "date_appointed", "date_ceased")
    list_filter = ("role", "is_signatory")
    search_fields = ("full_name", "entity__entity_name")


@admin.register(DepreciationAsset)
class DepreciationAssetAdmin(admin.ModelAdmin):
    list_display = ("asset_name", "category", "financial_year", "opening_wdv", "depreciation_amount", "closing_wdv")
    list_filter = ("category", "method")
    search_fields = ("asset_name", "category")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "user", "action", "description")
    list_filter = ("action",)
    search_fields = ("description",)
    readonly_fields = ("user", "action", "description", "affected_object_type",
                       "affected_object_id", "metadata", "timestamp", "ip_address")

# Tax Planning
from .models import TaxReferenceData, TaxPlanningWorksheet, TaxPlanningBeneficiaryRow, TaxPlanningScenario

@admin.register(TaxReferenceData)
class TaxReferenceDataAdmin(admin.ModelAdmin):
    list_display = ("financial_year_label", "key", "value", "description")
    list_filter = ("financial_year_label",)
    search_fields = ("key", "description")
    list_editable = ("value",)

@admin.register(TaxPlanningWorksheet)
class TaxPlanningWorksheetAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "status", "distributable_income", "last_updated_at")
    list_filter = ("status",)
    readonly_fields = ("created_at", "last_updated_at")

@admin.register(TaxPlanningBeneficiaryRow)
class TaxPlanningBeneficiaryRowAdmin(admin.ModelAdmin):
    list_display = ("beneficiary", "beneficiary_type", "proposed_distribution", "net_tax_payable", "effective_tax_rate")
    list_filter = ("beneficiary_type",)

@admin.register(TaxPlanningScenario)
class TaxPlanningScenarioAdmin(admin.ModelAdmin):
    list_display = ("scenario_name", "financial_year", "total_tax", "total_distributed", "created_at")

from .models import DocumentTemplate

@admin.register(DocumentTemplate)
class DocumentTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "document_category", "entity_type", "version", "is_active", "updated_at")
    list_filter = ("document_category", "entity_type", "is_active")
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "updated_at")

from .models import LegalDocumentTemplate, LegalDocument, GoverningDocument

@admin.register(LegalDocumentTemplate)
class LegalDocumentTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "document_type", "version", "is_active", "solicitor_approved", "created_at")
    list_filter = ("document_type", "is_active", "solicitor_approved")
    search_fields = ("name",)
    readonly_fields = ("created_at",)
    fieldsets = (
        (None, {
            "fields": ("name", "document_type", "entity_types", "template_file", "version"),
        }),
        ("Approval", {
            "fields": ("is_active", "solicitor_approved"),
        }),
        ("Schema", {
            "fields": ("variable_schema",),
            "classes": ("collapse",),
        }),
    )


@admin.register(LegalDocument)
class LegalDocumentAdmin(admin.ModelAdmin):
    list_display = ("entity", "document_type", "status", "version", "generated_at", "generated_by", "fusesign_status")
    list_filter = ("document_type", "status", "fusesign_status")
    search_fields = ("entity__entity_name",)
    readonly_fields = ("generated_at",)


@admin.register(GoverningDocument)
class GoverningDocumentAdmin(admin.ModelAdmin):
    list_display = ("entity", "document_type", "status", "is_primary", "extraction_status", "uploaded_at")
    list_filter = ("document_type", "status", "extraction_status", "is_primary")
    search_fields = ("entity__entity_name", "original_filename")
    readonly_fields = ("uploaded_at",)


# ---------------------------------------------------------------------------
# Phase 1-14 Models — corrected field names to match actual model definitions
# ---------------------------------------------------------------------------
from .models import (
    ActivityLog, RiskRule, RiskFlag, EntityRelationship,
    BulkJournalUpload, BASPeriod,
    EvaReview, EvaFinding, KnowledgeDocument, KnowledgeChunk,
    EvaConversation, EvaMessage, EvaTrustPlanningSession,
    TrustWorkspace, BeneficiaryProfile, DistributionScenario,
    Section100AAssessment, TrustElectionRecord,
    EvaClientSummary, DividendEvent, DividendShareholderAllocation,
    EngagementLetterConfig,
)


# --- ActivityLog ---
# Fields: id, user, event_type, title, description, entity, financial_year, url, is_read, created_at
@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "entity", "user", "event_type", "title", "created_at")
    list_filter = ("event_type",)
    search_fields = ("description", "entity__entity_name")
    readonly_fields = ("created_at",)


# --- RiskRule ---
# Fields: rule_id, category, title, description, severity, tier, applicable_entities, trigger_config,
#         recommended_action, legislation_ref, is_active, last_updated
@admin.register(RiskRule)
class RiskRuleAdmin(admin.ModelAdmin):
    list_display = ("rule_id", "title", "tier", "category", "severity", "is_active")
    list_filter = ("tier", "category", "is_active")
    search_fields = ("rule_id", "title")


# --- RiskFlag ---
# Fields: id, financial_year, run_id, rule_id, tier, severity, title, description, affected_accounts,
#         calculated_values, recommended_action, legislation_ref, status, resolution_notes, resolved_by,
#         resolved_at, created_at, ai_explanation, ai_suggested_action, ai_data_hash, ato_interest_score,
#         ato_interest_reasoning, ai_feedback, ai_feedback_notes
@admin.register(RiskFlag)
class RiskFlagAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "rule_id", "severity", "status", "created_at")
    list_filter = ("severity", "status")
    search_fields = ("financial_year__entity__entity_name", "title")
    readonly_fields = ("created_at",)


# --- EntityRelationship ---
# Fields: id, from_entity, to_entity, relationship_type, notes, created_by, created_at
@admin.register(EntityRelationship)
class EntityRelationshipAdmin(admin.ModelAdmin):
    list_display = ("from_entity", "to_entity", "relationship_type", "notes")
    list_filter = ("relationship_type",)
    search_fields = ("from_entity__entity_name", "to_entity__entity_name")


# --- BulkJournalUpload ---
# Fields: id, financial_year, reference_number, filename, description, lines_count,
#         total_debit, total_credit, uploaded_by, created_at
@admin.register(BulkJournalUpload)
class BulkJournalUploadAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "reference_number", "filename", "lines_count", "uploaded_by", "created_at")
    list_filter = ("financial_year__status",)
    readonly_fields = ("created_at",)


@admin.register(BASPeriod)
class BASPeriodAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "period_type", "period_start", "period_end", "status")
    list_filter = ("period_type", "status")
    search_fields = ("financial_year__entity__entity_name",)


# --- Eva AI Models ---

# --- EvaReview ---
# Fields: id, financial_year, triggered_at, completed_at, model_used, status, raw_response,
#         applicable_checks, triggered_by, error_message, error_acknowledged_by, error_acknowledged_at,
#         is_rerun, duration_seconds, opus_override
@admin.register(EvaReview)
class EvaReviewAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "status", "triggered_by", "triggered_at", "completed_at")
    list_filter = ("status",)
    search_fields = ("financial_year__entity__entity_name",)
    readonly_fields = ("triggered_at", "completed_at")


# --- EvaFinding ---
# Fields: id, eva_review, check_name, severity, title, plain_english_explanation, recommendation,
#         remediation_firm_procedure, remediation_authority, remediation_synthesis, legislation_reference,
#         knowledge_brain_citation, confidence, status, resolution_note, resolved_by, resolved_at, created_at
@admin.register(EvaFinding)
class EvaFindingAdmin(admin.ModelAdmin):
    list_display = ("eva_review", "check_name", "severity", "status", "created_at")
    list_filter = ("severity", "status", "check_name")
    search_fields = ("title", "plain_english_explanation")
    readonly_fields = ("created_at",)


# --- KnowledgeDocument ---
# Fields: id, title, category, sharepoint_path, sharepoint_item_id, sharepoint_modified_at,
#         sync_status, synced_at, chunk_count, file_type, file_size_bytes, is_archived, created_at, updated_at
@admin.register(KnowledgeDocument)
class KnowledgeDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "file_type", "sync_status", "synced_at")
    list_filter = ("category", "file_type", "sync_status")
    search_fields = ("title", "sharepoint_path")
    readonly_fields = ("synced_at", "created_at", "updated_at")


@admin.register(KnowledgeChunk)
class KnowledgeChunkAdmin(admin.ModelAdmin):
    list_display = ("document", "chunk_index", "token_count", "created_at")
    search_fields = ("text",)
    readonly_fields = ("created_at",)


# --- EvaConversation ---
# Fields: id, financial_year, user, created_at, last_active_at, message_count
@admin.register(EvaConversation)
class EvaConversationAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "user", "message_count", "created_at")
    list_filter = ("financial_year__status",)
    search_fields = ("financial_year__entity__entity_name",)
    readonly_fields = ("created_at",)


@admin.register(EvaMessage)
class EvaMessageAdmin(admin.ModelAdmin):
    list_display = ("conversation", "role", "model_used", "created_at")
    list_filter = ("role", "model_used")
    readonly_fields = ("created_at",)


# --- EvaTrustPlanningSession ---
# Fields: id, conversation, financial_year, triggered_at, triggered_by, net_distributable_income,
#         beneficiary_incomes_provided, recommended_distribution, final_distribution,
#         resolution_pre_populated, completed_at
@admin.register(EvaTrustPlanningSession)
class EvaTrustPlanningSessionAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "triggered_by", "triggered_at", "completed_at")
    readonly_fields = ("triggered_at", "completed_at")


# --- Trust Distribution Tab Models ---

# --- TrustWorkspace ---
# Fields: id, financial_year, stage_1_status, stage_2_status, stage_3_status, stage_4_status,
#         stage_5_status, stage_6_status, net_distributable_income, income_streams,
#         confirmed_scenario, section_100a_overall_risk, created_at, updated_at
@admin.register(TrustWorkspace)
class TrustWorkspaceAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "stage_1_status", "section_100a_overall_risk", "created_at")
    list_filter = ("stage_1_status", "section_100a_overall_risk")
    search_fields = ("financial_year__entity__entity_name",)
    readonly_fields = ("created_at",)


# --- BeneficiaryProfile ---
# Fields: id, trust_workspace, beneficiary, beneficiary_type, other_income, marginal_rate,
#         bracket_remaining, franking_surplus, include_in_distribution, exclusion_reason, tax_residency
@admin.register(BeneficiaryProfile)
class BeneficiaryProfileAdmin(admin.ModelAdmin):
    list_display = ("trust_workspace", "beneficiary", "beneficiary_type", "other_income", "marginal_rate")
    list_filter = ("beneficiary_type",)


# --- DistributionScenario ---
# Fields: id, trust_workspace, name, allocations, total_tax, tax_saved_vs_equal, is_confirmed, created_at
@admin.register(DistributionScenario)
class DistributionScenarioAdmin(admin.ModelAdmin):
    list_display = ("trust_workspace", "name", "total_tax", "is_confirmed", "created_at")
    list_filter = ("is_confirmed",)
    readonly_fields = ("created_at",)


# --- Section100AAssessment ---
# Fields: id, trust_workspace, beneficiary, q1-q8, risk_rating, resolution_strategy, reviewed_by, reviewed_at
@admin.register(Section100AAssessment)
class Section100AAssessmentAdmin(admin.ModelAdmin):
    list_display = ("trust_workspace", "beneficiary", "risk_rating", "reviewed_by")
    list_filter = ("risk_rating",)


# --- TrustElectionRecord ---
# Fields: id, trust_workspace, election_type, status, effective_date, test_individual,
#         related_entity, election_document, confirmed_by, confirmed_at
@admin.register(TrustElectionRecord)
class TrustElectionRecordAdmin(admin.ModelAdmin):
    list_display = ("trust_workspace", "election_type", "status", "confirmed_by")
    list_filter = ("election_type", "status")


# --- Compliance Document Models ---

@admin.register(EvaClientSummary)
class EvaClientSummaryAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "format_type", "version", "generated_at")
    list_filter = ("format_type",)
    search_fields = ("financial_year__entity__entity_name",)
    readonly_fields = ("generated_at",)


class DividendShareholderAllocationInline(admin.TabularInline):
    model = DividendShareholderAllocation
    extra = 0


@admin.register(DividendEvent)
class DividendEventAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "dividend_type", "total_amount", "franking_percentage", "declaration_date", "payment_date")
    list_filter = ("dividend_type",)
    search_fields = ("financial_year__entity__entity_name",)
    inlines = [DividendShareholderAllocationInline]


# --- DividendShareholderAllocation ---
# Fields: id, dividend_event, shareholder, shares_held, dividend_amount, franking_credit,
#         withholding_tax, dividend_statement
@admin.register(DividendShareholderAllocation)
class DividendShareholderAllocationAdmin(admin.ModelAdmin):
    list_display = ("dividend_event", "shareholder", "shares_held", "dividend_amount", "franking_credit")


# --- EngagementLetterConfig ---
# Fields: id, entity, services_engaged, fee_amount, fee_basis, additional_terms, last_generated_fy, updated_at
@admin.register(EngagementLetterConfig)
class EngagementLetterConfigAdmin(admin.ModelAdmin):
    list_display = ("entity", "services_engaged", "fee_basis", "fee_amount", "updated_at")
    list_filter = ("fee_basis",)
    search_fields = ("entity__entity_name",)
    readonly_fields = ("updated_at",)


@admin.register(BankAccountMapping)
class BankAccountMappingAdmin(admin.ModelAdmin):
    list_display = ("entity", "bank_account_name", "bsb", "account_number", "tb_account_code", "tb_account_name", "is_default")
    list_filter = ("is_default",)
    search_fields = ("entity__entity_name", "bank_account_name", "bsb", "account_number", "tb_account_code")
    readonly_fields = ("created_at", "updated_at")


# ---------------------------------------------------------------------------
# Division 7A Detection Module
# ---------------------------------------------------------------------------
from .models import Div7AAssessment, Div7ACompliance


@admin.register(Div7AAssessment)
class Div7AAssessmentAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "overall_severity", "total_exposure", "direct_loan_balance",
                    "upe_exposure", "escalation_required", "assessed_at")
    list_filter = ("overall_severity", "escalation_required")
    search_fields = ("financial_year__entity__entity_name",)
    readonly_fields = ("assessed_at",)
    fieldsets = (
        (None, {
            "fields": ("financial_year", "overall_severity", "assessed_at"),
        }),
        ("Position Detection", {
            "fields": ("direct_loan_balance", "direct_loan_accounts", "upe_exposure",
                       "upe_details", "s109e_payments", "s109e_details", "total_exposure"),
        }),
        ("Compliance Verification", {
            "fields": ("has_complying_agreement", "agreement_covers_balance",
                       "expected_interest", "recorded_interest", "interest_compliant",
                       "expected_myr", "actual_repayments", "myr_compliant"),
        }),
        ("Escalation", {
            "fields": ("escalation_required", "rules_fired"),
        }),
        ("Linked Finding", {
            "fields": ("eva_finding",),
        }),
    )


@admin.register(Div7ACompliance)
class Div7AComplianceAdmin(admin.ModelAdmin):
    list_display = ("entity", "borrower_name", "loan_amount", "loan_start_date",
                    "loan_term", "is_secured", "status", "last_reviewed")
    list_filter = ("status", "is_secured")
    search_fields = ("entity__entity_name", "borrower_name")
    readonly_fields = ("last_reviewed",)


from . import admin_office_admin  # noqa: F401
