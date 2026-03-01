from django.contrib import admin
from .models import (
    Client, Entity, FinancialYear, TrialBalanceLine,
    AccountMapping, ClientAccountMapping, NoteTemplate,
    AdjustingJournal, JournalLine, FinancialStatementTemplate,
    GeneratedDocument, AuditLog, EntityOfficer, DepreciationAsset,
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
# Phase 1-14 Models
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


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "entity", "user", "action", "created_at")
    list_filter = ("action",)
    search_fields = ("description", "entity__entity_name")
    readonly_fields = ("created_at",)


@admin.register(RiskRule)
class RiskRuleAdmin(admin.ModelAdmin):
    list_display = ("rule_id", "name", "tier", "category", "is_active")
    list_filter = ("tier", "category", "is_active")
    search_fields = ("rule_id", "name")


@admin.register(RiskFlag)
class RiskFlagAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "rule", "severity", "status", "created_at")
    list_filter = ("severity", "status")
    search_fields = ("financial_year__entity__entity_name",)
    readonly_fields = ("created_at",)


@admin.register(EntityRelationship)
class EntityRelationshipAdmin(admin.ModelAdmin):
    list_display = ("from_entity", "to_entity", "relationship_type", "ownership_percentage")
    list_filter = ("relationship_type",)
    search_fields = ("from_entity__entity_name", "to_entity__entity_name")


@admin.register(BulkJournalUpload)
class BulkJournalUploadAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "status", "total_rows", "processed_rows", "uploaded_by", "created_at")
    list_filter = ("status",)
    readonly_fields = ("created_at",)


@admin.register(BASPeriod)
class BASPeriodAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "period_type", "period_start", "period_end", "status")
    list_filter = ("period_type", "status")
    search_fields = ("financial_year__entity__entity_name",)


# --- Eva AI Models ---

@admin.register(EvaReview)
class EvaReviewAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "status", "triggered_by", "started_at", "completed_at")
    list_filter = ("status",)
    search_fields = ("financial_year__entity__entity_name",)
    readonly_fields = ("started_at", "completed_at")


@admin.register(EvaFinding)
class EvaFindingAdmin(admin.ModelAdmin):
    list_display = ("review", "check_id", "severity", "status", "created_at")
    list_filter = ("severity", "status", "check_id")
    search_fields = ("title", "explanation")
    readonly_fields = ("created_at",)


@admin.register(KnowledgeDocument)
class KnowledgeDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "source_type", "status", "last_synced_at")
    list_filter = ("category", "source_type", "status")
    search_fields = ("title", "source_path")
    readonly_fields = ("last_synced_at",)


@admin.register(KnowledgeChunk)
class KnowledgeChunkAdmin(admin.ModelAdmin):
    list_display = ("document", "chunk_index", "token_count", "created_at")
    search_fields = ("text",)
    readonly_fields = ("created_at",)


@admin.register(EvaConversation)
class EvaConversationAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "user", "interaction_type", "created_at")
    list_filter = ("interaction_type",)
    search_fields = ("financial_year__entity__entity_name",)
    readonly_fields = ("created_at",)


@admin.register(EvaMessage)
class EvaMessageAdmin(admin.ModelAdmin):
    list_display = ("conversation", "role", "model_used", "created_at")
    list_filter = ("role", "model_used")
    readonly_fields = ("created_at",)


@admin.register(EvaTrustPlanningSession)
class EvaTrustPlanningSessionAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "status", "created_at")
    list_filter = ("status",)
    readonly_fields = ("created_at",)


# --- Trust Distribution Tab Models ---

@admin.register(TrustWorkspace)
class TrustWorkspaceAdmin(admin.ModelAdmin):
    list_display = ("financial_year", "current_stage", "overall_100a_risk", "created_at")
    list_filter = ("current_stage", "overall_100a_risk")
    search_fields = ("financial_year__entity__entity_name",)
    readonly_fields = ("created_at",)


@admin.register(BeneficiaryProfile)
class BeneficiaryProfileAdmin(admin.ModelAdmin):
    list_display = ("workspace", "officer", "beneficiary_type", "estimated_taxable_income", "marginal_rate")
    list_filter = ("beneficiary_type",)


@admin.register(DistributionScenario)
class DistributionScenarioAdmin(admin.ModelAdmin):
    list_display = ("workspace", "name", "total_tax", "is_confirmed", "created_at")
    list_filter = ("is_confirmed",)
    readonly_fields = ("created_at",)


@admin.register(Section100AAssessment)
class Section100AAssessmentAdmin(admin.ModelAdmin):
    list_display = ("workspace", "beneficiary_profile", "risk_rating", "reviewed_by")
    list_filter = ("risk_rating",)


@admin.register(TrustElectionRecord)
class TrustElectionRecordAdmin(admin.ModelAdmin):
    list_display = ("workspace", "election_type", "status", "confirmed_by")
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


@admin.register(DividendShareholderAllocation)
class DividendShareholderAllocationAdmin(admin.ModelAdmin):
    list_display = ("dividend_event", "shareholder", "shares_held", "amount", "franking_credit")


@admin.register(EngagementLetterConfig)
class EngagementLetterConfigAdmin(admin.ModelAdmin):
    list_display = ("entity", "services_scope", "fee_basis", "last_generated_at")
    list_filter = ("fee_basis",)
    search_fields = ("entity__entity_name",)
    readonly_fields = ("last_generated_at",)


from . import admin_office_admin  # noqa: F401
