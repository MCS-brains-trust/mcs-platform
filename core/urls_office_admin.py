"""MCS Platform - Office Admin URL Configuration"""
from django.urls import path
from . import views_office_admin as views

app_name = "office_admin"

urlpatterns = [

    # Dashboard
    path("", views.office_admin_dashboard, name="dashboard"),

    # Task completion toggle (AJAX)
    path("tasks/<uuid:pk>/toggle/", views.toggle_task_completion, name="toggle_task"),

    # ── Client Correspondence ──
    path("correspondence/", views.correspondence_list, name="correspondence_list"),
    path("correspondence/incoming/", views.correspondence_incoming, name="correspondence_incoming"),
    path("correspondence/outgoing/", views.correspondence_outgoing, name="correspondence_outgoing"),
    path("correspondence/awaiting/", views.correspondence_awaiting, name="correspondence_awaiting"),
    path("correspondence/documents-in/", views.correspondence_documents_in, name="correspondence_documents_in"),
    path("correspondence/create/", views.correspondence_create, name="correspondence_create"),
    path("correspondence/<uuid:pk>/status/", views.correspondence_update_status, name="correspondence_update_status"),

    # ── ASIC / ATO ──
    path("noa/", views.noa_tracker, name="noa_tracker"),
    path("asic/", views.asic_returns_list, name="asic_returns"),
    path("asic/burning/", views.burning_list, name="burning_list"),
    path("asic/companies/", views.company_register, name="company_register"),

    # ── Legal Documents & ASIC Compliance ──
    path("legal-documents/", views.legal_documents_hub, name="legal_documents"),
    path("legal-documents/all/", views.legal_doc_all, name="legal_doc_all"),
    path("legal-documents/select-entity/<str:doc_type>/", views.legal_doc_select_entity, name="legal_doc_select_entity"),
    path("legal-documents/redirect/<str:doc_type>/<uuid:entity_pk>/", views.legal_doc_redirect_wizard, name="legal_doc_redirect_wizard"),
    path("api/legal-documents/entity-search/", views.legal_doc_entity_search_api, name="legal_doc_entity_search_api"),

    # ── Debtors ──
    path("debtors/", views.aged_receivables, name="aged_receivables"),
    path("debtors/statements-sent/", views.statements_sent, name="statements_sent"),
    path("debtors/overdue/", views.debtors_overdue, name="debtors_overdue"),
    path("debtors/payment-plans/", views.payment_plans_list, name="payment_plans"),
]
