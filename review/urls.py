"""Review app URL Configuration"""
from django.urls import path
from . import views
from . import views_enhanced

app_name = "review"

urlpatterns = [
    # Dashboard (homepage)
    path("", views.review_dashboard, name="dashboard"),

    # Review detail page
    path("review/<uuid:pk>/", views.review_detail, name="review_detail"),

    # AJAX endpoints — original
    path("api/review/transaction/<uuid:pk>/confirm/",
         views.confirm_transaction, name="confirm_transaction"),
    path("api/review/<uuid:pk>/submit/",
         views.submit_review, name="submit_review"),
    path("api/review/<uuid:pk>/accept-all/",
         views.accept_all_suggestions, name="accept_all"),

    # Upload bank statement (legacy — saves directly to DB)
    path("upload-statement/",
         views.upload_bank_statement, name="upload_statement"),

    # Parse-only endpoint (returns JSON, does NOT save to DB)
    path("parse-statement/",
         views.parse_statement, name="parse_statement"),

    # Upload preview page
    path("upload-preview/",
         views.upload_preview, name="upload_preview"),

    # Confirm import (saves verified transactions to DB)
    path("confirm-import/",
         views.confirm_import, name="confirm_import"),

    # Async classification
    path("api/review/<uuid:pk>/classify-batch/",
         views.classify_batch, name="classify_batch"),

    # Bulk approve by group
    path("api/review/<uuid:pk>/bulk-approve-group/",
         views.bulk_approve_group, name="bulk_approve_group"),

    # Webhook (n8n)
    path("api/notify/new-review-job/",
         views.notify_new_review_job, name="notify_new_job"),

    # -----------------------------------------------------------------------
    # Enhanced Workflow Endpoints (v2)
    # -----------------------------------------------------------------------

    # Natural Language Search (server-side fallback for compound queries)
    path("api/review/<uuid:pk>/search/",
         views_enhanced.search_transactions, name="search_transactions"),

    # Transaction Splitting
    path("api/review/transaction/<uuid:pk>/split/",
         views_enhanced.split_transaction, name="split_transaction"),
    path("api/review/transaction/<uuid:pk>/unsplit/",
         views_enhanced.unsplit_transaction, name="unsplit_transaction"),

    # Classification Rules (CRUD)
    path("api/review/rules/create/",
         views_enhanced.create_classification_rule, name="create_rule"),
    path("api/review/rules/<uuid:pk>/update/",
         views_enhanced.update_classification_rule, name="update_rule"),
    path("api/review/rules/<uuid:pk>/delete/",
         views_enhanced.delete_classification_rule, name="delete_rule"),
    path("api/review/rules/<uuid:pk>/toggle/",
         views_enhanced.toggle_classification_rule, name="toggle_rule"),
    path("api/review/rules/entity/<uuid:entity_id>/",
         views_enhanced.list_classification_rules, name="list_rules"),

    # GST Treatment Controls
    path("api/review/transaction/<uuid:pk>/gst-treatment/",
         views_enhanced.set_gst_treatment, name="set_gst_treatment"),
    path("api/review/<uuid:pk>/bulk-gst/",
         views_enhanced.bulk_set_gst_treatment, name="bulk_gst"),
    path("api/review/<uuid:pk>/undo-bulk-gst/",
         views_enhanced.undo_bulk_gst, name="undo_bulk_gst"),

    # GST Apportionment & Partial Credit
    path("api/review/transaction/<uuid:pk>/creditable-pct/",
         views_enhanced.set_creditable_percentage, name="set_creditable_pct"),
    path("api/review/transaction/<uuid:pk>/gst-override/",
         views_enhanced.set_gst_override, name="set_gst_override"),
    path("api/review/transaction/<uuid:pk>/detect-apportionment/",
         views_enhanced.detect_apportionment_api, name="detect_apportionment"),
    path("api/review/entity-gst-setting/",
         views_enhanced.save_entity_gst_setting, name="save_entity_gst_setting"),
]
