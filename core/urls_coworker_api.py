"""MCS Platform — Coworker Integration API URLs"""
from django.urls import path
from . import views_coworker_api as views

app_name = "coworker_api"

urlpatterns = [
    path("outreach-queue/", views.outreach_queue, name="outreach_queue"),
    path("entity/<uuid:entity_id>/", views.entity_detail, name="entity_detail"),
]
