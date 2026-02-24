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

from . import admin_office_admin  # noqa: F401
