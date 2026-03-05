"""
Object-level authorization utilities.

Prevents IDOR (Insecure Direct Object Reference) attacks by verifying
that the requesting user has permission to access specific entities,
financial years, and other scoped objects.
"""
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404


def _user_has_entity_access(user, entity):
    """
    Check whether *user* is allowed to access *entity*.
    Returns True if access is granted, False otherwise.

    Logic:
    - Admins / Senior Accountants / Office Admins → can_view_all_entities → True
    - Entity is directly assigned to user → True
    - Entity's client is assigned to user → True
    - Entity is unassigned (assigned_accountant is NULL) → deny for
      non-admin users to prevent unassigned entities leaking
    """
    if user.can_view_all_entities:
        return True

    # Entity directly assigned to this user
    if entity.assigned_accountant and entity.assigned_accountant == user:
        return True

    # Entity's parent client assigned to this user
    if entity.client and entity.client.assigned_accountant == user:
        return True

    return False


def get_entity_for_user(request, pk):
    """
    Retrieve an Entity by PK, verifying the user has access.
    Admins, Senior Accountants, and Office Admins can access all entities.
    Accountants can only access entities assigned to them (or their client).
    Unassigned entities are inaccessible to non-admin users.
    """
    from core.models import Entity

    entity = get_object_or_404(Entity, pk=pk)

    if not _user_has_entity_access(request.user, entity):
        raise PermissionDenied("You do not have access to this entity.")

    return entity


def get_financial_year_for_user(request, pk):
    """
    Retrieve a FinancialYear by PK, verifying the user has access
    to the parent entity.
    """
    from core.models import FinancialYear

    fy = get_object_or_404(
        FinancialYear.objects.select_related("entity", "entity__client"),
        pk=pk,
    )

    if not _user_has_entity_access(request.user, fy.entity):
        raise PermissionDenied("You do not have access to this financial year.")

    return fy


def get_review_job_for_user(request, pk):
    """
    Retrieve a ReviewJob by PK, verifying the user has access
    to the associated entity (if any).
    """
    from review.models import ReviewJob

    job = get_object_or_404(ReviewJob.objects.select_related("entity"), pk=pk)

    if request.user.can_view_all_entities:
        return job

    # If job is linked to an entity, check access
    if job.entity:
        if not _user_has_entity_access(request.user, job.entity):
            raise PermissionDenied("You do not have access to this review job.")

    return job
