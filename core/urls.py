"""MCS Platform - Core URL Configuration"""
from django.urls import path
from . import views
from . import views_audit
from . import views_upgrades

app_name = "core"

urlpatterns = [

    # Entities (top-level — replaces old Clients)
    path("entities/", views.entity_list, name="entity_list"),
    path("entities/create/", views.entity_create, name="entity_create"),
    path("entities/<uuid:pk>/", views.entity_detail, name="entity_detail"),
    path("entities/<uuid:pk>/edit/", views.entity_edit, name="entity_edit"),

    # Backward-compat: redirect old client_list URL
    path("clients/", views.entity_list, name="client_list"),

    # Financial Years
    path("entities/<uuid:entity_pk>/years/create/", views.financial_year_create, name="financial_year_create"),
    path("years/<uuid:pk>/", views.financial_year_detail, name="financial_year_detail"),
    path("years/<uuid:pk>/status/", views.financial_year_status, name="financial_year_status"),
    path("years/<uuid:pk>/roll-forward/", views.roll_forward, name="roll_forward"),

    # Trial Balance
    path("years/<uuid:pk>/import/", views.trial_balance_import, name="trial_balance_import"),
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
    path("years/<uuid:pk>/accounts-api/", views.account_list_api, name="account_list_api"),
    path("years/<uuid:pk>/journals/pdf/", views.journals_pdf, name="journals_pdf"),

    # Financial Statements Preview
    path("years/<uuid:pk>/statements/", views.financial_statements_view, name="financial_statements_view"),

    # Document Generation
    path("years/<uuid:pk>/generate/", views.generate_document, name="generate_document"),
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

    # Entity Chart of Accounts (per-entity customisation)
    path("years/<uuid:pk>/entity-coa/add/", views.entity_coa_add, name="entity_coa_add"),
    path("entity-account/<uuid:pk>/edit/", views.entity_coa_edit, name="entity_coa_edit"),
    path("entity-account/<uuid:pk>/delete/", views.entity_coa_delete, name="entity_coa_delete"),
    path("years/<uuid:pk>/entity-coa/suggest-code/", views.entity_coa_suggest_code, name="entity_coa_suggest_code"),
    path("years/<uuid:pk>/entity-coa/check-code/", views.entity_coa_check_code, name="entity_coa_check_code"),

    # Audit Library
    path("audit-library/", views_audit.audit_library, name="audit_library"),

    # Risk Flags & Risk Engine
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

    # GST Activity Statement
    path("years/<uuid:pk>/gst/", views.gst_activity_statement, name="gst_activity_statement"),
    path("years/<uuid:pk>/gst/download/", views.gst_activity_statement_download, name="gst_activity_statement_download"),

    # Depreciation
    path("years/<uuid:pk>/depreciation/add/", views.depreciation_add, name="depreciation_add"),
    path("depreciation/<uuid:pk>/edit/", views.depreciation_edit, name="depreciation_edit"),
    path("depreciation/<uuid:pk>/delete/", views.depreciation_delete, name="depreciation_delete"),
    path("years/<uuid:pk>/depreciation/roll-forward/", views.depreciation_roll_forward, name="depreciation_roll_forward"),
    path("years/<uuid:pk>/depreciation/post-to-tb/", views.depreciation_post_to_tb, name="depreciation_post_to_tb"),

    # Stock
    path("years/<uuid:pk>/stock/add/", views.stock_add, name="stock_add"),
    path("stock/<uuid:pk>/edit/", views.stock_edit, name="stock_edit"),
    path("stock/<uuid:pk>/delete/", views.stock_delete, name="stock_delete"),
    path("years/<uuid:pk>/stock/push-to-tb/", views.stock_push_to_tb, name="stock_push_to_tb"),

    # Review → Trial Balance
    path("years/<uuid:pk>/review/push-to-tb/", views.review_push_to_tb, name="review_push_to_tb"),
    path("review-txn/<uuid:pk>/approve/", views.review_approve_transaction, name="review_approve_transaction"),
    path("years/<uuid:pk>/review/approve-all/", views.review_approve_all, name="review_approve_all"),
    path("years/<uuid:pk>/review/classify-ai/", views.review_classify_ai, name="review_classify_ai"),
    path("years/<uuid:pk>/review/classify-status/", views.review_classify_status, name="review_classify_status"),
    path("years/<uuid:pk>/review/bulk-approve-group/", views.review_bulk_approve_group, name="review_bulk_approve_group"),

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
]
