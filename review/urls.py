"""Review app URL Configuration"""
from django.urls import path
from . import views

app_name = "review"

urlpatterns = [
    # Dashboard (homepage)
    path("", views.review_dashboard, name="dashboard"),

    # Review detail page
    path("review/<uuid:pk>/", views.review_detail, name="review_detail"),

    # AJAX endpoints
    path("api/review/transaction/<uuid:pk>/confirm/",
         views.confirm_transaction, name="confirm_transaction"),
    path("api/review/<uuid:pk>/submit/",
         views.submit_review, name="submit_review"),
    path("api/review/<uuid:pk>/accept-all/",
         views.accept_all_suggestions, name="accept_all"),

    # Upload bank statement (legacy — saves directly to DB)
    path("upload-statement/",
         views.upload_bank_statement, name="upload_statement"),

    # NEW: Parse-only endpoint (returns JSON, does NOT save to DB)
    path("parse-statement/",
         views.parse_statement, name="parse_statement"),

    # NEW: Upload preview page
    path("upload-preview/",
         views.upload_preview, name="upload_preview"),

    # NEW: Confirm import (saves verified transactions to DB)
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
]
