"""
Object-level authorization utilities.

Prevents IDOR (Insecure Direct Object Reference) attacks by verifying
that the requesting user has permission to access specific entities,
financial years, and other scoped objects.
"""
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404


def get_entity_for_user(request, pk):
    """
    Retrieve an Entity by PK, verifying the user has access.
    Admins, Senior Accountants, and Office Admins can access all entities.
    Accountants can only access entities assigned to them.
    Read-only users can view all entities (enforced at action level).
    """
    from core.models import Entity

    entity = get_object_or_404(Entity, pk=pk)

    if request.user.can_view_all_entities:
        return entity

    # Accountants and read-only: check assignment
    if entity.assigned_accountant and entity.assigned_accountant != request.user:
        # Also check if the entity's client is assigned to them
        if entity.client and entity.client.assigned_accountant == request.user:
            return entity
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

    if request.user.can_view_all_entities:
        return fy

    entity = fy.entity
    if entity.assigned_accountant and entity.assigned_accountant != request.user:
        if entity.client and entity.client.assigned_accountant == request.user:
            return fy
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
        entity = job.entity
        if entity.assigned_accountant and entity.assigned_accountant != request.user:
            if entity.client and entity.client.assigned_accountant == request.user:
                return job
            raise PermissionDenied("You do not have access to this review job.")

    return job
