"""MCS Platform - Core URL Configuration"""
from django.urls import path
from . import views
from . import views_audit
from . import views_bas
from . import views_upgrades
from . import views_tax_planning
from . import views_templates
from . import eva_chat
from . import eva_engine
from . import views_eva
from . import views_trust
from . import views_governing_docs
from . import views_engagement_letters
from . import views_legal_docs
from . import views_client_summary
from . import views_compliance_docs
from . import views_partnership_docs
from . import views_package_assembly
from . import views_bulk_operations
from . import views_bas_commentary
from . import views_div7a
from . import views_workpapers
from . import views_webhooks
from . import views_franking
from . import views_firm_settings
from . import views_family_trust_election

app_name = "core"

urlpatterns = [

    # Entities (top-level — replaces old Clients)
    path("entities/", views.entity_list, name="entity_list"),
    path("entities/create/", views.entity_create, name="entity_create"),
    path("entities/<uuid:pk>/", views.entity_detail, name="entity_detail"),
    path("entities/<uuid:pk>/edit/", views.entity_edit, name="entity_edit"),
    path("entities/<uuid:pk>/dismiss-legal-prompt/", views.dismiss_legal_doc_prompt, name="dismiss_legal_doc_prompt"),

    # Governing Documents
    path("entities/<uuid:pk>/governing-docs/upload/", views_governing_docs.governing_doc_upload, name="governing_doc_upload"),
    path("api/governing-docs/<uuid:doc_pk>/extract/", views_governing_docs.governing_doc_extract, name="governing_doc_extract"),
    path("api/governing-docs/<uuid:doc_pk>/status/", views_governing_docs.governing_doc_status, name="governing_doc_status"),
    path("api/governing-docs/<uuid:doc_pk>/archive/", views_governing_docs.governing_doc_archive, name="governing_doc_archive"),
    path("api/governing-docs/<uuid:doc_pk>/delete/", views_governing_docs.governing_doc_delete, name="governing_doc_delete"),

    # Legal Document Generation
    path("legal-templates/", views_legal_docs.legal_template_list, name="legal_template_list"),
    path("legal-templates/upload/", views_legal_docs.legal_template_upload, name="legal_template_upload"),
    path("legal-templates/<uuid:pk>/download/", views_legal_docs.legal_template_download, name="legal_template_download"),
    path("legal-templates/<uuid:pk>/replace/", views_legal_docs.legal_template_replace, name="legal_template_replace"),
    path("years/<uuid:pk>/legal-docs/wizard/<str:doc_type>/", views_legal_docs.legal_doc_wizard, name="legal_doc_wizard"),
    path("years/<uuid:pk>/legal-docs/generate/<str:doc_type>/", views_legal_docs.legal_doc_generate, name="legal_doc_generate"),
    path("years/<uuid:pk>/legal-docs/", views_legal_docs.legal_doc_list, name="legal_doc_list"),
    path("legal-docs/<uuid:doc_pk>/download/<str:fmt>/", views_legal_docs.legal_doc_download, name="legal_doc_download"),
    path("legal-docs/<uuid:doc_pk>/fusesign/", views_legal_docs.legal_doc_send_fusesign, name="legal_doc_send_fusesign"),
    path("api/legal-docs/entity-search/", views_legal_docs.legal_doc_entity_search, name="legal_doc_entity_search"),

    # Eva Client Summary
    path("years/<uuid:pk>/client-summary/", views_client_summary.client_summary_view, name="client_summary_view"),
    path("years/<uuid:pk>/client-summary/api/", views_client_summary.client_summary_api, name="client_summary_api"),
    path("years/<uuid:pk>/client-summary/generate/", views_client_summary.client_summary_generate, name="client_summary_generate"),

    # Company Compliance Documents
    path("years/<uuid:pk>/dividends/wizard/", views_compliance_docs.dividend_wizard, name="dividend_wizard"),
    path("years/<uuid:pk>/dividends/create/", views_compliance_docs.dividend_create, name="dividend_create"),
    path("years/<uuid:pk>/dividends/<uuid:event_pk>/", views_compliance_docs.dividend_detail, name="dividend_detail"),
    path("years/<uuid:pk>/compliance/solvency-resolution/", views_compliance_docs.generate_solvency_resolution, name="generate_solvency_resolution"),
    path("years/<uuid:pk>/compliance/directors-declaration/", views_compliance_docs.generate_directors_declaration, name="generate_directors_declaration"),
    path("years/<uuid:pk>/compliance/directors-report/", views_compliance_docs.directors_report_wizard, name="directors_report_wizard"),
    path("years/<uuid:pk>/compliance/directors-report/draft-eva/", views_compliance_docs.directors_report_draft_with_eva, name="directors_report_draft_eva"),
    path("years/<uuid:pk>/compliance/loan-acknowledgment/", views_compliance_docs.generate_loan_acknowledgment, name="generate_loan_acknowledgment"),
    path("years/<uuid:pk>/compliance/management-rep-letter/", views_compliance_docs.generate_management_rep_letter, name="generate_management_rep_letter"),
    path("years/<uuid:pk>/compliance/cover-letter/", views_compliance_docs.generate_cover_letter, name="generate_cover_letter"),

    # Franking Account
    path("years/<uuid:pk>/franking/", views_franking.franking_account_tab, name="franking_account_tab"),
    path("years/<uuid:pk>/franking/create/", views_franking.franking_entry_create, name="franking_entry_create"),
    path("years/<uuid:pk>/franking/<uuid:entry_pk>/delete/", views_franking.franking_entry_delete, name="franking_entry_delete"),
    path("years/<uuid:pk>/franking/summary/", views_franking.franking_account_summary_api, name="franking_account_summary_api"),

    # Partnership + Cross-Entity Documents
    path("years/<uuid:pk>/partner-statements/", views_partnership_docs.partner_statements, name="partner_statements"),
    path("years/<uuid:pk>/partner-statements/generate/", views_partnership_docs.generate_partner_statements, name="generate_partner_statements"),
    path("years/<uuid:pk>/partnership-tax-summary/", views_partnership_docs.generate_partnership_tax_summary, name="generate_partnership_tax_summary"),
    path("entities/<uuid:pk>/engagement-letter/", views_partnership_docs.engagement_letter_wizard, name="engagement_letter_wizard"),
    path("entities/<uuid:pk>/engagement-letter/generate/", views_legal_docs.engagement_letter_generate, name="engagement_letter_generate"),
    path("entities/<uuid:pk>/engagement-letter/quick-generate/", views_partnership_docs.engagement_letter_quick_generate, name="engagement_letter_quick_generate"),

    # Engagement Letters tab (per-year storage + audit trail)
    path("entities/<uuid:pk>/engagement-letters/upload/", views_engagement_letters.engagement_letter_upload, name="engagement_letter_upload"),
    path("api/engagement-letters/<uuid:letter_pk>/archive/", views_engagement_letters.engagement_letter_archive, name="engagement_letter_archive"),
    path("api/engagement-letters/<uuid:letter_pk>/delete/", views_engagement_letters.engagement_letter_delete, name="engagement_letter_delete"),
    path("api/engagement-letters/<uuid:letter_pk>/status/", views_engagement_letters.engagement_letter_update_status, name="engagement_letter_update_status"),

    # Package Assembly
    path("years/<uuid:pk>/package/", views_package_assembly.package_assembly, name="package_assembly"),
    path("years/<uuid:pk>/package/assemble/", views_package_assembly.package_assemble, name="package_assemble"),
    path("years/<uuid:pk>/package/send-for-signing/", views_package_assembly.package_send_for_signing, name="package_send_for_signing"),
    path("years/<uuid:pk>/package/download-bundle/", views_package_assembly.package_download_bundle, name="package_download_bundle"),

    # Year-End Commentary (Package Assembly)
    path("years/<uuid:pk>/package/commentary/generate/", views_package_assembly.yearend_commentary_generate, name="yearend_commentary_generate"),
    path("years/<uuid:pk>/package/commentary/poll/", views_package_assembly.yearend_commentary_poll, name="yearend_commentary_poll"),
    path("years/<uuid:pk>/package/commentary/save/", views_package_assembly.yearend_commentary_save, name="yearend_commentary_save"),
    path("years/<uuid:pk>/package/commentary/mark-reviewed/", views_package_assembly.yearend_commentary_mark_reviewed, name="yearend_commentary_mark_reviewed"),

    # Bulk Operations
    path("bulk/generate-packages/", views_bulk_operations.bulk_generate_packages, name="bulk_generate_packages"),
    path("bulk/readiness-check/", views_bulk_operations.bulk_readiness_check, name="bulk_readiness_check"),

    # Backward-compat: redirect old client_list URL
    path("clients/", views.entity_list, name="client_list"),

    # Financial Years
    path("entities/<uuid:entity_pk>/years/create/", views.financial_year_create, name="financial_year_create"),
    path("years/<uuid:pk>/", views.financial_year_detail, name="financial_year_detail"),
    path("years/<uuid:pk>/status/", views.financial_year_status, name="financial_year_status"),
    path("years/<uuid:pk>/finalise-full/", views.financial_year_finalise_full, name="financial_year_finalise_full"),
    path("years/<uuid:pk>/roll-forward/", views.roll_forward, name="roll_forward"),
    path("years/<uuid:pk>/reopen/", views.reopen_financial_year, name="reopen_financial_year"),
    path("years/<uuid:pk>/reroll-forward/", views.reroll_forward, name="reroll_forward"),
    path("api/years/<uuid:pk>/reroll-forward-diff/", views.reroll_forward_diff, name="reroll_forward_diff"),
    path("api/years/<uuid:pk>/reroll-forward-apply/", views.reroll_forward_apply, name="reroll_forward_apply"),

    # Trial Balance
    path("years/<uuid:pk>/import/", views.trial_balance_import, name="trial_balance_import"),
    path("years/<uuid:pk>/import/review/", views.review_tb_import, name="review_tb_import"),
    path("years/<uuid:pk>/import/commit/", views.commit_tb_import, name="commit_tb_import"),
    path("years/<uuid:pk>/trial-balance/", views.trial_balance_view, name="trial_balance_view"),
    path("years/<uuid:pk>/trial-balance/pdf/", views.trial_balance_pdf, name="trial_balance_pdf"),
    path("years/<uuid:pk>/trial-balance/download/", views.trial_balance_download, name="trial_balance_download"),
    path("years/<uuid:pk>/trial-balance/account/<str:account_code>/", views.account_code_breakdown, name="account_code_breakdown"),
    path("trial-balance/line/<uuid:pk>/reallocate/", views.tb_line_reallocate, name="tb_line_reallocate"),
    path("trial-balance/template/", views.trial_balance_template_download, name="trial_balance_template_download"),

    # Account Mapping
    path("mapping/", views.account_mapping_list, name="account_mapping_list"),
    path("mapping/create/", views.account_mapping_create, name="account_mapping_create"),
    path("years/<uuid:pk>/map-accounts/", views.map_client_accounts, name="map_client_accounts"),

    # Journal Entries
    path("years/<uuid:pk>/adjustments/", views.adjustment_list, name="adjustment_list"),
    path("years/<uuid:pk>/adjustments/create/", views.adjustment_create, name="adjustment_create"),
    path("journals/<uuid:pk>/", views.journal_detail, name="journal_detail"),
    path("journals/<uuid:pk>/post/", views.journal_post, name="journal_post"),
    path("journals/<uuid:pk>/delete/", views.journal_delete, name="journal_delete"),
    path("journals/<uuid:pk>/edit/", views.journal_edit, name="journal_edit"),
    path("years/<uuid:pk>/accounts-api/", views.account_list_api, name="account_list_api"),
    path("years/<uuid:pk>/journals/pdf/", views.journals_pdf, name="journals_pdf"),
    path("years/<uuid:pk>/journals/upload/", views.journal_upload, name="journal_upload"),
    path("years/<uuid:pk>/journals/upload/review/", views.review_journal_upload, name="review_journal_upload"),
    path("years/<uuid:pk>/journals/upload/commit/", views.commit_journal_upload, name="commit_journal_upload"),
    path("journals/template/", views.journal_template_download, name="journal_template_download"),
    path("years/<uuid:pk>/calculate-tax-journal/", views.calculate_tax_journal, name="calculate_tax_journal"),
    path("years/<uuid:pk>/tax-provision/status/", views.tax_provision_status, name="tax_provision_status"),
    path("years/<uuid:pk>/tax-provision/post/", views.auto_tax_provision, name="auto_tax_provision"),

    # Financial Statements Preview
    path("years/<uuid:pk>/statements/", views.financial_statements_view, name="financial_statements_view"),
    path("years/<uuid:pk>/statements/line-item/<str:standard_code>/", views.line_item_breakdown, name="line_item_breakdown"),

    # Document Generation
    path("years/<uuid:pk>/generate/", views.generate_document, name="generate_document"),
    path("years/<uuid:pk>/management-accounts/", views.generate_management_accounts_view, name="generate_management_accounts"),
    path("years/<uuid:pk>/distribution-minutes/", views.generate_distribution_minutes, name="generate_distribution_minutes"),
    path("documents/<uuid:pk>/delete/", views.delete_document, name="delete_document"),

    # Entity Officers / Signatories
    path("entities/<uuid:pk>/officers/", views.entity_officers, name="entity_officers"),
    path("entities/<uuid:entity_pk>/officers/create/", views.entity_officer_create, name="entity_officer_create"),
    path("officers/<uuid:pk>/edit/", views.entity_officer_edit, name="entity_officer_edit"),
    path("officers/<uuid:pk>/delete/", views.entity_officer_delete, name="entity_officer_delete"),

    # Access Ledger Import (admin)
    path("import/access-ledger/", views.access_ledger_import, name="access_ledger_import"),

    # Chart of Accounts
    path("chart-of-accounts/", views_audit.chart_of_accounts, name="chart_of_accounts"),
    path("chart-of-accounts/add/", views_audit.coa_add, name="coa_add"),
    path("chart-of-accounts/<uuid:pk>/edit/", views_audit.coa_edit, name="coa_edit"),
    path("chart-of-accounts/<uuid:pk>/delete/", views_audit.coa_delete, name="coa_delete"),
    path("chart-of-accounts/check-code/", views_audit.coa_check_code, name="coa_check_code"),
    path("chart-of-accounts/suggest-code/", views_audit.coa_suggest_code, name="coa_suggest_code"),
    path("api/chart-of-accounts/", views_audit.chart_of_accounts_api, name="chart_of_accounts_api"),
    path("chart-of-accounts/propagate-tax-codes/", views_audit.coa_propagate_tax_codes, name="coa_propagate_tax_codes"),

    # Entity Chart of Accounts (per-entity customisation)
    path("years/<uuid:pk>/entity-coa/add/", views.entity_coa_add, name="entity_coa_add"),
    path("entity-account/<uuid:pk>/edit/", views.entity_coa_edit, name="entity_coa_edit"),
    path("entity-account/<uuid:pk>/delete/", views.entity_coa_delete, name="entity_coa_delete"),
    path("years/<uuid:pk>/entity-coa/suggest-code/", views.entity_coa_suggest_code, name="entity_coa_suggest_code"),
    path("years/<uuid:pk>/entity-coa/check-code/", views.entity_coa_check_code, name="entity_coa_check_code"),

    # Audit Library
    path("audit-library/", views_audit.audit_library, name="audit_library"),

    # Risk Flags & Risk Engine
    path("years/<uuid:pk>/risk-badge/", views.risk_badge_api, name="risk_badge_api"),
    path("years/<uuid:pk>/risk-flags/", views_audit.risk_flags_view, name="risk_flags"),
    path("risk-flags/<uuid:pk>/resolve/", views_audit.resolve_risk_flag, name="resolve_risk_flag"),
    path("years/<uuid:pk>/run-risk-engine/", views_audit.run_risk_engine_view, name="run_risk_engine"),
    path("risk-flags/<uuid:pk>/ai-analyse/", views_audit.ai_analyse_flag, name="ai_analyse_flag"),
    path("years/<uuid:pk>/ai-analyse-all/", views_audit.ai_analyse_all_flags, name="ai_analyse_all_flags"),
    path("years/<uuid:pk>/ai-prioritise/", views_audit.ai_prioritise_flags, name="ai_prioritise_flags"),
    path("years/<uuid:pk>/risk-report/", views_audit.generate_risk_report, name="generate_risk_report"),
    path("risk-flags/<uuid:pk>/ai-feedback/", views_audit.ai_feedback_view, name="ai_feedback"),

    # Associates (now entity-level)
    path("entities/<uuid:entity_pk>/associates/create/", views.associate_create, name="associate_create"),
    path("associates/<uuid:pk>/edit/", views.associate_edit, name="associate_edit"),
    path("associates/<uuid:pk>/delete/", views.associate_delete, name="associate_delete"),

    # Entity-to-Entity Relationships
    path("api/entity-link-search/", views.entity_link_search, name="entity_link_search"),
    path("entities/<uuid:entity_pk>/link/", views.entity_link_create, name="entity_link_create"),
    path("entity-links/<uuid:pk>/delete/", views.entity_link_delete, name="entity_link_delete"),

    # Accounting Software (now entity-level)
    path("entities/<uuid:entity_pk>/software/create/", views.software_create, name="software_create"),
    path("software/<uuid:pk>/edit/", views.software_edit, name="software_edit"),
    path("software/<uuid:pk>/delete/", views.software_delete, name="software_delete"),

    # Meeting Notes (now entity-level)
    path("entities/<uuid:entity_pk>/notes/create/", views.meeting_note_create, name="meeting_note_create"),
    path("notes/<uuid:pk>/", views.meeting_note_detail, name="meeting_note_detail"),
    path("notes/<uuid:pk>/edit/", views.meeting_note_edit, name="meeting_note_edit"),
    path("notes/<uuid:pk>/delete/", views.meeting_note_delete, name="meeting_note_delete"),
    path("notes/<uuid:pk>/toggle-followup/", views.meeting_note_toggle_followup, name="meeting_note_toggle_followup"),

    # GST Activity Statement (Period-Aware Redesign)
    path("years/<uuid:pk>/gst/", views_bas.bas_dashboard, name="gst_activity_statement"),
    path("years/<uuid:pk>/gst/download/", views_bas.bas_download, name="gst_activity_statement_download"),
    path("years/<uuid:pk>/gst/lodge/<int:period_number>/", views_bas.bas_lodge_period, name="bas_lodge_period"),
    path("years/<uuid:pk>/gst/unlodge/<int:period_number>/", views_bas.bas_unlodge_period, name="bas_unlodge_period"),
    path("years/<uuid:pk>/gst/coverage/<int:period_number>/", views_bas.bas_coverage_check, name="bas_coverage_check"),
    path("years/<uuid:pk>/gst/reallocate/", views_bas.bas_reallocate_transaction, name="bas_reallocate_transaction"),
    path("years/<uuid:pk>/gst/bulk-reallocate/", views_bas.bas_bulk_reallocate, name="bas_bulk_reallocate"),
    path("years/<uuid:pk>/gst/accounts/", views_bas.bas_entity_accounts_json, name="bas_entity_accounts_json"),

    # BAS Period Commentary
    path("years/<uuid:pk>/gst/commentary/generate/", views_bas_commentary.generate_commentary, name="bas_commentary_generate"),
    path("years/<uuid:pk>/gst/commentary/list/", views_bas_commentary.list_commentaries, name="bas_commentary_list"),
    path("commentary/<uuid:pk>/", views_bas_commentary.get_commentary, name="bas_commentary_detail"),
    path("commentary/<uuid:pk>/update/", views_bas_commentary.update_commentary, name="bas_commentary_update"),
    path("commentary/<uuid:pk>/regenerate/", views_bas_commentary.regenerate_commentary, name="bas_commentary_regenerate"),
    path("commentary/<uuid:pk>/download/", views_bas_commentary.download_commentary, name="bas_commentary_download"),
    path("commentary/<uuid:pk>/status/", views_bas_commentary.commentary_status, name="bas_commentary_status"),
    path("commentary/<uuid:pk>/mark-sent/", views_bas_commentary.mark_commentary_sent, name="bas_commentary_mark_sent"),
    path("commentary/<uuid:pk>/delete/", views_bas_commentary.delete_commentary, name="bas_commentary_delete"),
    path("years/<uuid:pk>/gst/commentary/compare/", views_bas_commentary.compare_commentaries, name="bas_commentary_compare"),

    # Depreciation
    path("years/<uuid:pk>/depreciation/add/", views.depreciation_add, name="depreciation_add"),
    path("years/<uuid:pk>/depreciation/suggest-account-code/", views.depreciation_suggest_account_code, name="depreciation_suggest_account_code"),
    path("years/<uuid:pk>/depreciation/create-account/", views.depreciation_create_account, name="depreciation_create_account"),
    path("depreciation/<uuid:pk>/edit/", views.depreciation_edit, name="depreciation_edit"),
    path("depreciation/<uuid:pk>/delete/", views.depreciation_delete, name="depreciation_delete"),
    path("years/<uuid:pk>/depreciation/roll-forward/", views.depreciation_roll_forward, name="depreciation_roll_forward"),
    path("years/<uuid:pk>/depreciation/post-to-tb/", views.depreciation_post_to_tb, name="depreciation_post_to_tb"),
    path("years/<uuid:pk>/depreciation/pdf/", views.depreciation_pdf, name="depreciation_pdf"),
    path("depreciation/add-from-transaction/<uuid:pk>/", views.depreciation_add_from_transaction, name="depreciation_add_from_transaction"),

    # Stock
    path("years/<uuid:pk>/stock/add/", views.stock_add, name="stock_add"),
    path("stock/<uuid:pk>/edit/", views.stock_edit, name="stock_edit"),
    path("stock/<uuid:pk>/delete/", views.stock_delete, name="stock_delete"),
    path("years/<uuid:pk>/stock/push-to-tb/", views.stock_push_to_tb, name="stock_push_to_tb"),

    # Review → Trial Balance
    path("years/<uuid:pk>/review/push-to-tb/", views.review_push_to_tb, name="review_push_to_tb"),
    path("review-txn/<uuid:pk>/approve/", views.review_approve_transaction, name="review_approve_transaction"),
    path("review-txn/<uuid:pk>/unconfirm/", views.review_unconfirm_transaction, name="review_unconfirm_transaction"),
    path("years/<uuid:pk>/review/approve-all/", views.review_approve_all, name="review_approve_all"),
    path("years/<uuid:pk>/review/classify-ai/", views.review_classify_ai, name="review_classify_ai"),
    path("years/<uuid:pk>/review/classify-status/", views.review_classify_status, name="review_classify_status"),
    path("years/<uuid:pk>/review/bulk-approve-group/", views.review_bulk_approve_group, name="review_bulk_approve_group"),
    path("years/<uuid:pk>/review/approve-selected/", views.review_approve_selected, name="review_approve_selected"),
    path("years/<uuid:pk>/review/export-pdf/", views.review_export_pdf, name="review_export_pdf"),

    # Bank Statement Template Download
    path("bank-statement/template/", views.bank_statement_template_download, name="bank_statement_template_download"),

    # Bank Account Mapping
    path("years/<uuid:pk>/review/bank-mapping/", views.review_bank_account_mapping, name="review_bank_account_mapping"),
    path("years/<uuid:pk>/review/recalculate-bank-contra/", views.recalculate_bank_contra_entries, name="recalculate_bank_contra_entries"),

    # Opening Balance Validation API
    path("years/<uuid:pk>/review/validate-opening-balance/", views.review_validate_opening_balance, name="review_validate_opening_balance"),

    # Opening Balance Journal
    path("years/<uuid:pk>/review/opening-balance/", views.review_post_opening_balance, name="review_post_opening_balance"),

    # Bulk Edit Transactions
    path("years/<uuid:pk>/review/bulk-edit/", views.review_bulk_edit_transactions, name="review_bulk_edit_transactions"),

    # Delete Transactions
    path("years/<uuid:pk>/review/delete-transaction/<uuid:txn_pk>/", views.review_delete_transaction, name="review_delete_transaction"),
    path("years/<uuid:pk>/review/delete-all/", views.review_delete_all_transactions, name="review_delete_all_transactions"),
    path("years/<uuid:pk>/review/delete-selected/", views.review_delete_selected_transactions, name="review_delete_selected_transactions"),

    # Notifications / Activity
    path("api/notifications/", views.notifications_api, name="notifications_api"),
    path("api/notifications/<uuid:pk>/read/", views.mark_notification_read, name="mark_notification_read"),
    path("api/notifications/read-all/", views.mark_all_notifications_read, name="mark_all_notifications_read"),

    # HTMX partials
    path("htmx/entity-search/", views.htmx_client_search, name="htmx_client_search"),
    path("htmx/tb-line/<uuid:pk>/map/", views.htmx_map_tb_line, name="htmx_map_tb_line"),

    # Bulk Actions (entity-level)
    path("entities/bulk-action/", views.entity_bulk_action, name="entity_bulk_action"),

    # Entity-level HandiLedger Import
    path("entities/<uuid:pk>/import-handiledger/", views.entity_import_handiledger, name="entity_import_handiledger"),

    # Delete Unfinalised FY Data
    path("entities/<uuid:pk>/delete-unfinalised/", views.delete_unfinalised_fy, name="delete_unfinalised_fy"),

    # HTMX: Update TB Line Mapping
    path("htmx/tb-line/<uuid:pk>/update-mapping/", views.htmx_update_tb_mapping, name="htmx_update_tb_mapping"),

    # COA Search API (for review tab dropdown)
    path("api/coa-search/", views.coa_search_api, name="coa_search_api"),
    path("years/<uuid:pk>/api/entity-coa-search/", views.entity_coa_search_api, name="entity_coa_search_api"),

    # XRM Pull (Xero Practice Manager) — now entity-level
    path("entities/<uuid:pk>/xrm-search/", views.xrm_search, name="xrm_search"),
    path("entities/<uuid:pk>/xrm-pull/", views.xrm_pull, name="xrm_pull"),

    # ===== UPGRADE 1: Prior Year Comparatives Engine =====
    path("years/<uuid:pk>/comparatives/populate/", views_upgrades.populate_comparatives, name="populate_comparatives"),
    path("tb-line/<uuid:pk>/comparative/override/", views_upgrades.override_comparative, name="override_comparative"),
    path("years/<uuid:pk>/comparatives/lock/", views_upgrades.lock_comparatives, name="lock_comparatives"),

    # ===== UPGRADE 2: Document Version Control & Regeneration =====
    path("years/<uuid:pk>/regenerate/", views_upgrades.regenerate_document, name="regenerate_document"),
    path("years/<uuid:pk>/bulk-regenerate/", views_upgrades.bulk_regenerate, name="bulk_regenerate"),
    path("documents/<uuid:doc_pk>/mark-final/", views_upgrades.mark_document_final, name="mark_document_final"),

    # ===== UPGRADE 4: Trust Distribution Workflow =====
    path("years/<uuid:pk>/distribution/", views_upgrades.trust_distribution, name="trust_distribution"),
    path("years/<uuid:pk>/beneficiary-statement/<uuid:officer_pk>/", views_upgrades.generate_beneficiary_statement, name="generate_beneficiary_statement"),

    # ===== UPGRADE 5: Partnership Profit Allocation =====
    path("years/<uuid:pk>/partnership/", views_upgrades.partnership_allocation, name="partnership_allocation"),
    path("years/<uuid:pk>/partner-statement/<uuid:officer_pk>/", views_upgrades.generate_partner_statement, name="generate_partner_statement"),

    # ===== UPGRADE 6: Working Paper Notes =====
    path("years/<uuid:pk>/workpaper-notes/", views_upgrades.workpaper_notes_api, name="workpaper_notes_api"),
    path("years/<uuid:pk>/workpaper-notes/carry-forward/", views_upgrades.carry_forward_notes, name="carry_forward_notes"),
    path("years/<uuid:pk>/workpaper-notes/export/", views_upgrades.export_workpaper_notes, name="export_workpaper_notes"),

    # ===== UPGRADE 7: Bulk Entity Import =====
    path("import/bulk/", views_upgrades.bulk_import_start, name="bulk_import_start"),
    path("import/bulk/template/", views_upgrades.bulk_import_template, name="bulk_import_template"),
    path("import/bulk/<uuid:pk>/map/", views_upgrades.bulk_import_map, name="bulk_import_map"),
    path("import/bulk/<uuid:pk>/validate/", views_upgrades.bulk_import_validate, name="bulk_import_validate"),
    path("import/bulk/<uuid:pk>/execute/", views_upgrades.bulk_import_execute, name="bulk_import_execute"),

    # ===== ENTITY ASSIGNMENTS (Sprint 2) =====
    path("assignments/", views_upgrades.entity_assignments, name="entity_assignments"),
    path("assignments/bulk-assign/", views_upgrades.bulk_assign_entities, name="bulk_assign_entities"),
    path("entities/<uuid:pk>/assign/", views_upgrades.update_entity_assignment, name="update_entity_assignment"),

    # ===== TRUST TAX PLANNING WORKSHEET =====
    path("years/<uuid:pk>/tax-planning/", views_tax_planning.tax_planning_tab, name="tax_planning_tab"),
    path("years/<uuid:pk>/tax-planning/calculate/", views_tax_planning.tax_planning_calculate, name="tax_planning_calculate"),
    path("years/<uuid:pk>/tax-planning/save/", views_tax_planning.tax_planning_save, name="tax_planning_save"),
    path("years/<uuid:pk>/tax-planning/save-notes/", views_tax_planning.tax_planning_save_notes, name="tax_planning_save_notes"),
    path("years/<uuid:pk>/tax-planning/scenario/save/", views_tax_planning.tax_planning_scenario_save, name="tax_planning_scenario_save"),
    path("years/<uuid:pk>/tax-planning/scenario/<uuid:scenario_pk>/delete/", views_tax_planning.tax_planning_scenario_delete, name="tax_planning_scenario_delete"),
    path("years/<uuid:pk>/tax-planning/scenario/<uuid:scenario_pk>/apply/", views_tax_planning.tax_planning_scenario_apply, name="tax_planning_scenario_apply"),
    path("years/<uuid:pk>/tax-planning/finalise/", views_tax_planning.tax_planning_finalise, name="tax_planning_finalise"),
    path("years/<uuid:pk>/tax-planning/reopen/", views_tax_planning.tax_planning_reopen, name="tax_planning_reopen"),
    path("years/<uuid:pk>/trust-election/", views_tax_planning.generate_trust_election_view, name="generate_trust_election"),
    path("years/<uuid:pk>/tax-planning-summary/", views_tax_planning.generate_tax_planning_summary_view, name="generate_tax_planning_summary"),
    # ===== Live Net Profit API =====
    path("years/<uuid:pk>/api/net-profit/", views.net_profit_api, name="net_profit_api"),

    # ===== Bulk Journal Uploads =====
    path("bulk-journals/<uuid:pk>/", views.bulk_journal_detail, name="bulk_journal_detail"),
    path("bulk-journals/<uuid:pk>/delete/", views.bulk_journal_delete, name="bulk_journal_delete"),
    path("bulk-journals/<uuid:pk>/reallocate/", views.bulk_journal_reallocate, name="bulk_journal_reallocate"),
    path("bulk-journals/line/<uuid:pk>/delete/", views.bulk_journal_line_delete, name="bulk_journal_line_delete"),

    # ===== DOCUMENT TEMPLATE MANAGER =====
    path("templates/", views_templates.template_list, name="template_list"),
    path("templates/create/", views_templates.template_create, name="template_create"),
    path("templates/<uuid:pk>/edit/", views_templates.template_edit, name="template_edit"),
    path("templates/<uuid:pk>/preview/", views_templates.template_preview, name="template_preview"),
    path("templates/<uuid:pk>/new-version/", views_templates.template_new_version, name="template_new_version"),
    path("templates/<uuid:pk>/delete/", views_templates.template_delete, name="template_delete"),
    path("templates/<uuid:pk>/toggle-active/", views_templates.template_toggle_active, name="template_toggle_active"),
    path("templates/<uuid:pk>/update-structure/", views_templates.template_update_structure, name="template_update_structure"),
    # FS template admin endpoints (download/replace .docx)
    path("templates/fs/<uuid:pk>/download/", views_templates.fs_template_download, name="fs_template_download"),
    path("templates/fs/<uuid:pk>/replace/", views_templates.fs_template_replace, name="fs_template_replace"),
    # Workpaper template admin endpoints
    path("templates/workpapers/upload/", views_templates.workpaper_template_upload, name="workpaper_template_upload"),
    path("templates/workpapers/<uuid:pk>/download/", views_templates.workpaper_template_download, name="workpaper_template_download"),
    path("templates/workpapers/<uuid:pk>/replace/", views_templates.workpaper_template_replace, name="workpaper_template_replace"),
    path("templates/workpapers/<uuid:pk>/delete/", views_templates.workpaper_template_delete, name="workpaper_template_delete"),

    # ===== EVA AI PRACTICE INTELLIGENCE =====
    # Chat Interface (GET = history, POST = send message)
    path("api/financial-years/<uuid:pk>/eva-chat/", eva_chat.eva_chat_dispatch, name="eva_chat_api"),

    # Finalisation Gate
    path("api/financial-years/<uuid:pk>/ask-eva-review/", eva_engine.ask_eva_review, name="ask_eva_review"),
    path("api/financial-years/<uuid:pk>/eva-review/", eva_engine.eva_review_detail, name="eva_review_detail"),
    path("api/financial-years/<uuid:pk>/eva-review-status/", eva_engine.eva_review_status, name="eva_review_status"),
    path("api/eva-findings/<uuid:pk>/resolve/", eva_engine.eva_finding_resolve, name="eva_resolve_finding"),
    path("api/eva-findings/<uuid:pk>/clarify/", eva_engine.eva_clarify_finding, name="eva_clarify_finding"),
    path("api/eva-findings/<uuid:pk>/clarify-question/", eva_engine.eva_clarification_question, name="eva_clarification_question"),
    path("api/eva-findings/<uuid:pk>/auto-disclose-rp/", eva_engine.eva_auto_disclose_rp, name="eva_auto_disclose_rp"),
    path("api/financial-years/<uuid:pk>/activity/", eva_engine.financial_year_activity, name="financial_year_activity"),
    path("api/financial-years/<uuid:pk>/eva-preflight/", eva_engine.eva_preflight, name="eva_preflight"),
    path("api/financial-years/<uuid:pk>/eva-rerun/", views_eva.eva_rerun_review, name="eva_rerun_review"),
    path("api/financial-years/<uuid:pk>/eva-finalise/", views_eva.eva_finalise, name="eva_finalise"),
    path("api/financial-years/<uuid:fy_pk>/override-suppression/<uuid:suppression_pk>/", views_eva.override_suppression, name="override_suppression"),

    # Knowledge Brain
    path("api/knowledge/sync/", eva_engine.knowledge_sync, name="knowledge_sync"),
    path("api/knowledge/documents/", eva_engine.knowledge_documents, name="knowledge_documents"),
    path("api/knowledge/search/", eva_engine.knowledge_search, name="knowledge_search"),
    path("api/knowledge/status/", eva_engine.knowledge_status, name="knowledge_status"),
    path("eva/knowledge-brain/", views_eva.knowledge_brain_admin, name="knowledge_brain_admin"),
    path("eva/knowledge-brain/sync/", views_eva.trigger_knowledge_sync, name="trigger_knowledge_sync"),

    # ===== DIVISION 7A DETECTION MODULE =====
    path("years/<uuid:pk>/div7a/", views_div7a.div7a_dashboard, name="div7a_dashboard"),
    path("years/<uuid:pk>/div7a/run/", views_div7a.div7a_run_assessment, name="div7a_run_assessment"),
    path("years/<uuid:pk>/div7a/api/", views_div7a.div7a_assessment_api, name="div7a_assessment_api"),
    path("years/<uuid:pk>/div7a/compliance/create/", views_div7a.div7a_compliance_create, name="div7a_compliance_create"),
    path("entities/<uuid:pk>/div7a/compliance/", views_div7a.div7a_compliance_list, name="div7a_compliance_list"),
    path("div7a/compliance/<uuid:pk>/edit/", views_div7a.div7a_compliance_edit, name="div7a_compliance_edit"),

    # ===== TRUST DISTRIBUTION TAB =====
    path("api/years/<uuid:pk>/trust-workspace/", views_trust.trust_workspace_api, name="trust_workspace_api"),
    path("api/years/<uuid:pk>/trust-workspace/stage/<int:stage_num>/", views_trust.trust_stage_update, name="trust_stage_update"),
    path("api/years/<uuid:pk>/trust-workspace/beneficiaries/", views_trust.beneficiary_profiles_api, name="beneficiary_profiles_api"),
    path("api/years/<uuid:pk>/trust-workspace/scenarios/", views_trust.distribution_scenarios_api, name="distribution_scenarios_api"),
    path("api/years/<uuid:pk>/trust-workspace/scenarios/<uuid:scenario_pk>/confirm/", views_trust.confirm_scenario, name="confirm_scenario"),
    path("api/years/<uuid:pk>/trust-workspace/scenarios/<uuid:scenario_pk>/delete/", views_trust.delete_scenario, name="delete_scenario"),
    path("api/years/<uuid:pk>/trust-workspace/section-100a/", views_trust.section_100a_api, name="section_100a_api"),
    path("api/years/<uuid:pk>/trust-workspace/elections/", views_trust.trust_elections_api, name="trust_elections_api"),
    path("api/years/<uuid:pk>/trust-workspace/elections/<uuid:election_pk>/confirm/", views_trust.confirm_election, name="confirm_election"),
    path("api/years/<uuid:pk>/trust-workspace/eva-context/", views_trust.trust_eva_context, name="trust_eva_context"),
    path("api/years/<uuid:pk>/trust-workspace/generate/beneficiary-statements/", views_trust.trust_generate_beneficiary_statements, name="trust_generate_beneficiary_statements"),
    path("api/years/<uuid:pk>/trust-workspace/generate/distribution-summary/", views_trust.trust_generate_distribution_summary, name="trust_generate_distribution_summary"),
    path("api/years/<uuid:pk>/trust-workspace/generate/100a-summary/", views_trust.trust_generate_100a_summary, name="trust_generate_100a_summary"),
    path("api/years/<uuid:pk>/trust-workspace/post-distribution/", views_trust.trust_post_distribution, name="trust_post_distribution"),

    # ===== WORK PAPERS TAB =====
    path("years/<uuid:fy_pk>/workpapers/<uuid:template_pk>/download/", views_workpapers.workpaper_download, name="workpaper_download"),
    path("api/years/<uuid:fy_pk>/workpapers/", views_workpapers.workpaper_list_api, name="workpaper_list_api"),

    # ===== FIRM SETTINGS =====
    # NOTE: Must NOT use the admin/ prefix — Django's built-in admin site
    # intercepts all /admin/* requests before core.urls is consulted.
    path("settings/firm/", views_firm_settings.firm_settings, name="firm_settings"),

    # ===== FAMILY TRUST ELECTION =====
    path("entities/<uuid:entity_pk>/family-trust-election/", views_family_trust_election.family_trust_election, name="family_trust_election_new"),
    path("entities/<uuid:entity_pk>/family-trust-election/<uuid:doc_pk>/", views_family_trust_election.family_trust_election, name="family_trust_election_edit"),
    path("api/fte/<uuid:doc_pk>/toggle-checklist/", views_family_trust_election.fte_toggle_checklist, name="fte_toggle_checklist"),
    path("api/fte/<uuid:doc_pk>/delete/", views_family_trust_election.fte_delete, name="fte_delete"),

    # ===== WEBHOOKS (third-party callbacks) =====
    path("webhooks/fusesign/", views_webhooks.fusesign_webhook, name="fusesign_webhook"),
    path("webhooks/textract/", views_webhooks.textract_webhook, name="textract_webhook"),
]
