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

    # ── Debtors ──
    path("debtors/", views.aged_receivables, name="aged_receivables"),
    path("debtors/statements-sent/", views.statements_sent, name="statements_sent"),
    path("debtors/overdue/", views.debtors_overdue, name="debtors_overdue"),
    path("debtors/payment-plans/", views.payment_plans_list, name="payment_plans"),
]
