"""
Eva Client Summary module (Phase 10).

This module re-exports the generate_client_summary function from
eva_client_summary.py to match the import path used in tasks.py:
    from core.eva_summary import generate_client_summary

Spec reference: Master Implementation Spec §2.6, §7.10
"""
from core.eva_client_summary import generate_client_summary  # noqa: F401

__all__ = ["generate_client_summary"]
