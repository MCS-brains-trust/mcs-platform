"""
Xero Practice Manager (XPM) Sync Engine.

Handles the full sync lifecycle:
1. OAuth2 connection (reuses Xero OAuth2 with practice manager scopes)
2. Pull clients from XPM → create/update Client + Entity in StatementHub
3. Pull contacts → create/update ClientAssociate records
4. Pull relationships → link associates to related clients/entities
5. Pull notes → create MeetingNote records

XPM API v3.1 returns XML. We parse it into Python dicts.
Base URL: https://api.xero.com/practicemanager/3.1/
"""
import logging
import xml.etree.ElementTree as ET
from datetime import timedelta
from typing import Optional

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import Client, Entity, ClientAssociate, MeetingNote
from .models import XPMConnection, XPMSyncLog

logger = logging.getLogger(__name__)

XPM_BASE_URL = "https://api.xero.com/practicemanager/3.1/"

# XPM Business Structure → StatementHub entity_type mapping
STRUCTURE_MAP = {
    "Company": "company",
    "Trust": "trust",
    "Partnership": "partnership",
    "SoleTrader": "sole_trader",
    "Sole Trader": "sole_trader",
    "Individual": "individual",
    "SuperFund": "smsf",
    "Super Fund": "smsf",
    "SMSF": "smsf",
    "NonProfit": "company",
    "Non Profit": "company",
    "Other": "company",
}

# XPM Relationship Type → ClientAssociate relationship_type mapping
RELATIONSHIP_MAP = {
    "Shareholder": "shareholder",
    "Director": "director",
    "Owner": "related_entity",
    "Partnership": "related_entity",
    "Trustee": "related_entity",
    "Beneficiary": "related_entity",
    "Secretary": "related_entity",
    "Spouse": "spouse",
    "Child": "child",
    "Parent": "parent",
    "Sibling": "sibling",
}


def _ensure_valid_token(connection: XPMConnection) -> bool:
    """Refresh the XPM access token if needed.

    The XPM refresh token is single-use and rotates on every refresh. To avoid
    two concurrent syncs racing the rotation (the loser would get invalid_grant
    and mark the connection expired), we lock the connection row and re-check
    needs_refresh after acquiring the lock.
    """
    if not connection.needs_refresh:
        return True

    with transaction.atomic():
        # Lock the connection row so concurrent refreshes serialise.
        locked = XPMConnection.objects.select_for_update().get(pk=connection.pk)

        # Another process may have refreshed while we waited for the lock.
        if not locked.needs_refresh:
            # Copy the freshly-rotated tokens back into the caller's instance.
            connection.access_token = locked.access_token
            connection.refresh_token = locked.refresh_token
            connection.token_expires_at = locked.token_expires_at
            connection.status = locked.status
            return True

        try:
            resp = requests.post(
                "https://login.xero.com/identity/connect/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": locked.refresh_token,
                    "client_id": getattr(settings, "XERO_CLIENT_ID", ""),
                    "client_secret": getattr(settings, "XERO_CLIENT_SECRET", ""),
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            locked.access_token = data["access_token"]
            locked.refresh_token = data.get("refresh_token", locked.refresh_token)
            locked.token_expires_at = timezone.now() + timedelta(
                seconds=data.get("expires_in", 1800)
            )
            locked.status = "active"
            locked.last_error = ""
            locked.save()

            # Reflect the new tokens on the caller's instance.
            connection.access_token = locked.access_token
            connection.refresh_token = locked.refresh_token
            connection.token_expires_at = locked.token_expires_at
            connection.status = locked.status
            connection.last_error = ""
            return True
        except Exception as e:
            logger.error(f"XPM token refresh failed: {e}")
            locked.status = "expired"
            locked.last_error = str(e)
            locked.save()
            connection.status = locked.status
            connection.last_error = locked.last_error
            return False


def _xpm_get(connection: XPMConnection, endpoint: str, params: dict = None) -> Optional[ET.Element]:
    """Make an authenticated GET request to the XPM API. Returns XML root element."""
    url = f"{XPM_BASE_URL}{endpoint}"
    headers = {
        "Authorization": f"Bearer {connection.access_token}",
        "Xero-Tenant-Id": connection.tenant_id,
        "Accept": "application/xml",
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        return ET.fromstring(resp.text)
    except requests.exceptions.HTTPError as e:
        logger.error(f"XPM API error {endpoint}: {e} - {e.response.text if e.response else ''}")
        raise
    except Exception as e:
        logger.error(f"XPM API error {endpoint}: {e}")
        raise


def _xml_text(element: ET.Element, tag: str, default: str = "") -> str:
    """Safely extract text from an XML element."""
    child = element.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return default


def _xml_uuid(element: ET.Element, tag: str) -> str:
    """Extract a UUID from a nested element."""
    child = element.find(tag)
    if child is not None:
        uuid_el = child.find("UUID")
        if uuid_el is not None and uuid_el.text:
            return uuid_el.text.strip()
    return ""


def _xml_name(element: ET.Element, tag: str) -> str:
    """Extract a Name from a nested element."""
    child = element.find(tag)
    if child is not None:
        name_el = child.find("Name")
        if name_el is not None and name_el.text:
            return name_el.text.strip()
    return ""


# ---------------------------------------------------------------------------
# Client Sync
# ---------------------------------------------------------------------------

def sync_clients(connection: XPMConnection, sync_log: XPMSyncLog) -> dict:
    """
    Pull all clients from XPM and create/update in StatementHub.
    Returns stats dict.
    """
    stats = {"created": 0, "updated": 0, "entities_created": 0, "errors": []}

    try:
        root = _xpm_get(connection, "client.api/list")
    except Exception as e:
        stats["errors"].append(f"Failed to fetch client list: {e}")
        return stats

    clients_el = root.findall(".//Client")
    logger.info(f"XPM sync: found {len(clients_el)} clients")

    for client_el in clients_el:
        try:
            _sync_single_client(connection, client_el, stats)
        except Exception as e:
            xpm_id = _xml_text(client_el, "UUID")
            logger.error(f"Error syncing XPM client (uuid={xpm_id}): {e}")
            stats["errors"].append(f"Client sync error (uuid={xpm_id}): {e}")

    return stats


def _sync_single_client(connection: XPMConnection, client_el: ET.Element, stats: dict):
    """Sync a single client from XPM list response."""
    xpm_uuid = _xml_text(client_el, "UUID")
    name = _xml_text(client_el, "Name")
    email = _xml_text(client_el, "Email")
    phone = _xml_text(client_el, "Phone")

    if not name:
        return

    # Try to find existing client by xpm_client_id
    client = Client.objects.filter(xpm_client_id=xpm_uuid).first()

    if not client:
        # Try fuzzy match by name
        client = Client.objects.filter(name__iexact=name).first()

    if client:
        # Update existing
        updated = False
        if xpm_uuid and client.xpm_client_id != xpm_uuid:
            client.xpm_client_id = xpm_uuid
            updated = True
        if email and not client.contact_email:
            client.contact_email = email
            updated = True
        if phone and not client.contact_phone:
            client.contact_phone = phone
            updated = True
        if updated:
            client.save()
            stats["updated"] += 1
    else:
        # Create new client
        client = Client.objects.create(
            name=name,
            contact_email=email,
            contact_phone=phone,
            xpm_client_id=xpm_uuid,
        )
        stats["created"] += 1

    # Now fetch detailed client data for entity creation
    if xpm_uuid:
        try:
            _sync_client_detail(connection, client, xpm_uuid, stats)
        except Exception as e:
            logger.warning(f"Could not fetch detail for XPM client (uuid={xpm_uuid}): {e}")


def _sync_client_detail(connection: XPMConnection, client: Client, xpm_uuid: str, stats: dict):
    """Fetch detailed client info and create/update entity + contacts."""
    try:
        root = _xpm_get(connection, f"client.api/get/{xpm_uuid}")
    except Exception as e:
        # Don't swallow silently: a failed detail fetch means the entity,
        # contacts, relationships and notes for this client are missing.
        logger.error(f"XPM client detail fetch failed (uuid={xpm_uuid}): {e}")
        stats.setdefault("errors", []).append(
            f"Client detail fetch failed (uuid={xpm_uuid}): {e}"
        )
        return

    client_el = root.find(".//Client")
    if client_el is None:
        client_el = root

    # Business structure → entity type
    structure = _xml_text(client_el, "BusinessStructure")
    entity_type = STRUCTURE_MAP.get(structure, "")

    # ABN / ACN / Tax Number
    abn = _xml_text(client_el, "BusinessNumber")
    acn = _xml_text(client_el, "CompanyNumber")
    tax_number = _xml_text(client_el, "TaxNumber")  # masked

    # GST
    gst_registered = _xml_text(client_el, "GSTRegistered", "").lower() == "yes"

    # Account Manager
    account_manager = _xml_name(client_el, "AccountManager")

    # Create or update entity if we have a business structure
    if entity_type and entity_type != "individual":
        entity_name = _xml_text(client_el, "Name")
        entity = Entity.objects.filter(
            client=client, entity_name__iexact=entity_name
        ).first()

        if not entity:
            entity = Entity.objects.create(
                client=client,
                entity_name=entity_name,
                entity_type=entity_type,
                abn=abn,
                acn=acn,
                is_gst_registered=gst_registered,
            )
            stats["entities_created"] += 1
        else:
            updated = False
            if abn and not entity.abn:
                entity.abn = abn
                updated = True
            if acn and not entity.acn:
                entity.acn = acn
                updated = True
            if entity_type and entity.entity_type != entity_type:
                entity.entity_type = entity_type
                updated = True
            if updated:
                entity.save()

    # Sync contacts
    contacts_el = client_el.find("Contacts")
    if contacts_el is not None:
        for contact_el in contacts_el.findall("Contact"):
            _sync_contact(client, contact_el)

    # Sync relationships
    relationships_el = client_el.find("Relationships")
    if relationships_el is not None:
        for rel_el in relationships_el.findall("Relationship"):
            _sync_relationship(client, rel_el)

    # Sync notes
    notes_el = client_el.find("Notes")
    if notes_el is not None:
        for note_el in notes_el.findall("Note"):
            _sync_note(client, note_el)


def _sync_contact(client: Client, contact_el: ET.Element):
    """Sync a single XPM contact as a ClientAssociate."""
    xpm_uuid = _xml_text(contact_el, "UUID")
    name = _xml_text(contact_el, "Name")
    email = _xml_text(contact_el, "Email")
    phone = _xml_text(contact_el, "Phone")
    mobile = _xml_text(contact_el, "Mobile")
    position = _xml_text(contact_el, "Position")
    is_primary = _xml_text(contact_el, "IsPrimary", "").lower() == "true"

    if not name:
        return

    # Determine relationship type from position
    position_lower = (position or "").lower()
    if "spouse" in position_lower or "wife" in position_lower or "husband" in position_lower:
        rel_type = "spouse"
    elif "child" in position_lower or "son" in position_lower or "daughter" in position_lower:
        rel_type = "child"
    elif "director" in position_lower:
        rel_type = "director"
    elif "secretary" in position_lower:
        rel_type = "secretary"
    elif "shareholder" in position_lower:
        rel_type = "shareholder"
    else:
        rel_type = "other"

    # Find or create
    assoc = ClientAssociate.objects.filter(
        client=client, xpm_contact_uuid=xpm_uuid
    ).first() if xpm_uuid else None

    if not assoc:
        assoc = ClientAssociate.objects.filter(
            client=client, name__iexact=name
        ).first()

    if assoc:
        # Update
        if xpm_uuid and not assoc.xpm_contact_uuid:
            assoc.xpm_contact_uuid = xpm_uuid
        if email and not assoc.email:
            assoc.email = email
        if (phone or mobile) and not assoc.phone:
            assoc.phone = phone or mobile
        if position and not assoc.occupation:
            assoc.occupation = position
        assoc.save()
    else:
        ClientAssociate.objects.create(
            client=client,
            name=name,
            relationship_type=rel_type,
            email=email,
            phone=phone or mobile,
            occupation=position,
            xpm_contact_uuid=xpm_uuid,
        )


def _sync_relationship(client: Client, rel_el: ET.Element):
    """Sync a single XPM relationship."""
    rel_type_xpm = _xml_text(rel_el, "Type")
    related_uuid = _xml_uuid(rel_el, "RelatedClient")
    related_name = _xml_name(rel_el, "RelatedClient")

    if not related_name:
        return

    rel_type = RELATIONSHIP_MAP.get(rel_type_xpm, "related_entity")

    # Try to find the related client in StatementHub
    related_client = None
    if related_uuid:
        related_client = Client.objects.filter(xpm_client_id=related_uuid).first()
    if not related_client:
        related_client = Client.objects.filter(name__iexact=related_name).first()

    # Find or create associate
    assoc = ClientAssociate.objects.filter(
        client=client, name__iexact=related_name
    ).first()

    if assoc:
        if related_client and not assoc.related_client:
            assoc.related_client = related_client
        if rel_type and assoc.relationship_type == "other":
            assoc.relationship_type = rel_type
        assoc.save()
    else:
        ClientAssociate.objects.create(
            client=client,
            name=related_name,
            relationship_type=rel_type,
            related_client=related_client,
        )


def _sync_note(client: Client, note_el: ET.Element):
    """Sync a single XPM note as a MeetingNote."""
    title = _xml_text(note_el, "Title")
    text = _xml_text(note_el, "Text")
    folder = _xml_text(note_el, "Folder")
    date_str = _xml_text(note_el, "Date")
    created_by = _xml_text(note_el, "CreatedBy")

    if not title and not text:
        return

    if not title:
        title = f"XPM Note - {folder}" if folder else "XPM Note"

    # Parse date
    meeting_date = None
    if date_str:
        try:
            from datetime import datetime
            # XPM dates are typically ISO format
            meeting_date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
        except (ValueError, TypeError):
            meeting_date = timezone.now().date()
    else:
        meeting_date = timezone.now().date()

    # Check for duplicate
    existing = MeetingNote.objects.filter(
        client=client,
        title=title,
        meeting_date=meeting_date,
    ).first()

    if existing:
        return  # Don't duplicate

    MeetingNote.objects.create(
        client=client,
        title=title,
        meeting_date=meeting_date,
        meeting_type="other",
        discussion_points=text,
        tags=f"xpm-import,{folder}" if folder else "xpm-import",
        notes=f"Imported from XPM. Created by: {created_by}" if created_by else "Imported from XPM.",
    )


# ---------------------------------------------------------------------------
# Full Sync Orchestrator
# ---------------------------------------------------------------------------

def run_full_sync(connection: XPMConnection, user=None) -> XPMSyncLog:
    """
    Run a full sync from XPM to StatementHub.
    Creates a sync log and returns it.
    """
    if not _ensure_valid_token(connection):
        raise Exception("XPM token is expired and could not be refreshed.")

    sync_log = XPMSyncLog.objects.create(
        connection=connection,
        sync_type="full",
        status="running",
        started_by=user,
    )

    all_errors = []

    try:
        # 1. Sync clients (includes contacts, relationships, notes from detail)
        client_stats = sync_clients(connection, sync_log)
        sync_log.clients_created = client_stats["created"]
        sync_log.clients_updated = client_stats["updated"]
        sync_log.entities_created = client_stats["entities_created"]
        all_errors.extend(client_stats["errors"])

        # Update connection stats
        connection.last_sync_at = timezone.now()
        connection.clients_synced = Client.objects.filter(xpm_client_id__gt="").count()
        connection.contacts_synced = ClientAssociate.objects.filter(xpm_contact_uuid__gt="").count()
        connection.save()

        # Determine final status
        if all_errors:
            sync_log.status = "partial"
        else:
            sync_log.status = "completed"

    except Exception as e:
        logger.error(f"XPM full sync failed: {e}")
        all_errors.append(f"Sync failed: {e}")
        sync_log.status = "failed"
        connection.last_error = str(e)
        connection.save()

    sync_log.errors = all_errors
    sync_log.completed_at = timezone.now()
    sync_log.save()

    return sync_log
