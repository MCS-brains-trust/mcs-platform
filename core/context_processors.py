"""
MCS Platform - Context Processors
Provides sidebar badge counts, role-based navigation data, and
active-nav detection to all templates.
"""
from datetime import timedelta
from django.db.models import Q
from django.utils import timezone


def office_admin_context(request):
    """
    Inject sidebar badge counts and active_nav into every template context
    for ALL authenticated users.
    """
    if not request.user.is_authenticated:
        return {}

    is_office_admin = getattr(request.user, 'is_office_admin', False)

    # ── Sidebar badge counts ──
    from .models_office_admin import (
        Correspondence, ASICReturn, NOARecord, DebtorRecord,
    )
    from .models import LegalDocument

    today = timezone.now().date()
    seven_days = today + timedelta(days=7)

    try:
        correspondence_new = Correspondence.objects.filter(date_received=today).count()
        correspondence_awaiting = Correspondence.objects.filter(status="awaiting").count()
        documents_in = Correspondence.objects.filter(
            direction="incoming",
            correspondence_type__in=["tax_documents", "bank_statement", "other"],
            status__in=["received", "pending"],
        ).count()

        asic_burning = ASICReturn.objects.filter(
            Q(status="burning") | Q(due_date__lte=seven_days, status__in=["pending", "overdue"])
        ).count()

        overdue_count = DebtorRecord.objects.filter(status="overdue").count()

        pending_signature = LegalDocument.objects.filter(
            fusesign_status="sent"
        ).count()
    except Exception:
        correspondence_new = 0
        correspondence_awaiting = 0
        documents_in = 0
        asic_burning = 0
        overdue_count = 0
        pending_signature = 0

    # ── Active nav detection from URL path ──
    path = request.path
    active_nav = _detect_active_nav(path)

    return {
        'is_office_admin': is_office_admin,
        'sidebar_correspondence_new': correspondence_new,
        'sidebar_correspondence_awaiting': correspondence_awaiting,
        'sidebar_documents_in': documents_in,
        'sidebar_asic_burning': asic_burning,
        'sidebar_overdue_count': overdue_count,
        'sidebar_pending_signature': pending_signature,
        'active_nav': active_nav,
    }


def _detect_active_nav(path):
    """
    Map the current URL path to a sidebar nav item identifier.
    Returns the active_nav string that matches the sidebar template.
    """
    # Office Admin section paths
    if path.startswith('/office-admin/correspondence/incoming'):
        return 'incoming'
    if path.startswith('/office-admin/correspondence/outgoing'):
        return 'outgoing'
    if path.startswith('/office-admin/correspondence/awaiting'):
        return 'awaiting'
    if path.startswith('/office-admin/correspondence/documents-in'):
        return 'documents_in'
    if path.startswith('/office-admin/correspondence'):
        return 'incoming'  # Default correspondence page
    if path.startswith('/office-admin/noa'):
        return 'noa'
    if path.startswith('/office-admin/asic/burning'):
        return 'burning'
    if path.startswith('/office-admin/asic/companies'):
        return 'company_register'
    if path.startswith('/office-admin/asic'):
        return 'asic_returns'
    if path.startswith('/office-admin/debtors/statements-sent'):
        return 'statements_sent'
    if path.startswith('/office-admin/debtors/overdue'):
        return 'overdue'
    if path.startswith('/office-admin/debtors/payment-plans'):
        return 'payment_plans'
    if path.startswith('/office-admin/debtors'):
        return 'aged_receivables'
    if path.startswith('/office-admin/legal-documents'):
        return 'legal_documents'
    if path.startswith('/office-admin'):
        return 'dashboard'

    # Core section paths
    if path.startswith('/entities') or path.startswith('/clients'):
        return 'entity_hub'
    if path.startswith('/years/'):
        return 'entity_hub'
    if path.startswith('/audit-library'):
        return 'audit_library'

    # Admin section paths
    if path.startswith('/accounts/users'):
        return 'user_management'
    if path.startswith('/assignments'):
        return 'entity_assignments'
    if path.startswith('/chart-of-accounts'):
        return 'chart_of_accounts'
    if path.startswith('/templates'):
        return 'document_templates'
    if path.startswith('/integrations/connections'):
        return 'connections'
    if path.startswith('/import/bulk'):
        return 'import'
    if path.startswith('/admin/firm-settings'):
        return 'firm_settings'

    # Dashboard (root or review dashboard)
    if path == '/' or path == '':
        return 'dashboard'

    return ''
