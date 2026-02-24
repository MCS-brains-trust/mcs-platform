"""
MCS Platform - Context Processors
Provides sidebar badge counts and role-based navigation data to all templates.
"""
from datetime import timedelta
from django.db.models import Q
from django.utils import timezone


def office_admin_context(request):
    """
    Inject Office Admin sidebar counts into every template context.
    Only queries the database if the user has the office_admin role.
    """
    if not request.user.is_authenticated:
        return {}

    # Check if user has office_admin role (uses the is_office_admin property on User model)
    is_office_admin = getattr(request.user, 'is_office_admin', False)

    if not is_office_admin:
        return {'is_office_admin': False}

    # Import here to avoid circular imports
    from .models_office_admin import (
        Correspondence, ASICReturn, NOARecord, DebtorRecord,
    )

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
    except Exception:
        # Tables may not exist yet during migration
        correspondence_new = 0
        correspondence_awaiting = 0
        documents_in = 0
        asic_burning = 0
        overdue_count = 0

    return {
        'is_office_admin': True,
        'sidebar_correspondence_new': correspondence_new,
        'sidebar_correspondence_awaiting': correspondence_awaiting,
        'sidebar_documents_in': documents_in,
        'sidebar_asic_burning': asic_burning,
        'sidebar_overdue_count': overdue_count,
    }
