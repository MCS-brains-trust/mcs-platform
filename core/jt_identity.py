# core/jt_identity.py
"""Client identity, read live from Job Tracker.

StatementHub keeps everything JT does not hold — chart of accounts, trial
balances, officers, capital accounts, depreciation. It stops owning IDENTITY:
legal name, entity type, ABN, registered address, TFN. Those live in Job
Tracker, which mirrors XPM, and XPM remains the sole source of truth. Nothing
here writes back.

Fail-soft is the contract, not a nicety: a statement being prepared must never
stall because JT is restarting. Every error arm returns _unavailable(), the
caller renders SH's last-known values, and the page says so plainly. This
mirrors CoWorker's chat/jt_jobs.py, the established pattern for JT calls.
"""
import logging
from dataclasses import dataclass, field

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# JT caps this endpoint's page size at 25; asking for more is silently trimmed
# server-side, so trim here too and keep the two numbers honest.
SEARCH_HARD_MAX = 25
MIN_QUERY_LENGTH = 2


@dataclass(frozen=True)
class IdentityResult:
    """Three-state fetch outcome.

    state == "ok"          `fields` is JT's typed-absence envelope
    state == "not_found"   JT has no client with that XPM id — the link is wrong
    state == "unavailable" we could not ask; render last-known and say so

    Frozen so no call site can quietly turn an unavailable into an ok.
    """

    state: str
    fields: dict = field(default_factory=dict)

    @property
    def ok(self):
        return self.state == "ok"

    def held(self, name):
        """The value of a `held` field, or None.

        `not_held` (JT holds nothing) and `restricted` (TFN, masked unless PII is
        requested) are both absences as far as a rendered statement is concerned.
        """
        entry = self.fields.get(name)
        if isinstance(entry, dict) and entry.get("status") == "held":
            return entry.get("value")
        return None


@dataclass(frozen=True)
class ClientSearchResult:
    failed: bool
    clients: list = field(default_factory=list)


def _unavailable():
    """The fail-soft sentinel. Grep this name to audit every failure path."""
    return IdentityResult(state="unavailable")


def _search_failed():
    return ClientSearchResult(failed=True, clients=[])


def _headers():
    return {"x-service-auth": settings.JT_INTERNAL_SERVICE_SECRET}


def _base():
    return settings.JT_INTERNAL_BASE_URL.rstrip("/")


def fetch_identity(xpm_client_id):
    """GET /api/internal/clients/{xpm_client_id}/identity, fail-soft.

    TFN comes back masked: includePii is deliberately never sent. A financial
    statement needs the name, type, ABN and address, not the TFN itself.
    """
    xpm_client_id = (xpm_client_id or "").strip()
    if not xpm_client_id:
        # Not an error and not worth a network call: an unlinked entity simply
        # has no JT counterpart yet.
        return IdentityResult(state="not_found")

    url = f"{_base()}/clients/{xpm_client_id}/identity"
    try:
        response = requests.get(
            url, headers=_headers(), timeout=settings.JT_IDENTITY_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as exc:
        logger.warning("jt.identity.network_error xpm=%s err=%s", xpm_client_id, exc.__class__.__name__)
        return _unavailable()

    if response.status_code == 404:
        logger.info("jt.identity.not_found xpm=%s", xpm_client_id)
        return IdentityResult(state="not_found")

    if response.status_code != 200:
        # 403 here almost always means SH's egress IP fell out of JT's nginx
        # allow-list (a droplet rebuild does it silently). See the comment in
        # job-tracker/nginx-job-tracker.conf.
        logger.warning("jt.identity.non_200 xpm=%s status=%s", xpm_client_id, response.status_code)
        return _unavailable()

    try:
        payload = response.json()
    except ValueError:
        logger.warning("jt.identity.bad_json xpm=%s", xpm_client_id)
        return _unavailable()

    fields = payload.get("fields") if isinstance(payload, dict) else None
    if not isinstance(fields, dict):
        logger.warning("jt.identity.bad_envelope xpm=%s", xpm_client_id)
        return _unavailable()

    return IdentityResult(state="ok", fields=fields)


def search_clients(query, limit=10):
    """GET /api/internal/clients/search?q=…, fail-soft.

    Used by the create-entity flow to pick the JT client whose XPM id gets
    stored on the new entity. A failure returns no rows and the operator types
    the id by hand — never a blocked form.
    """
    query = (query or "").strip()
    if len(query) < MIN_QUERY_LENGTH:
        return ClientSearchResult(failed=False, clients=[])

    try:
        response = requests.get(
            f"{_base()}/clients/search",
            headers=_headers(),
            params={"q": query, "limit": min(int(limit), SEARCH_HARD_MAX)},
            timeout=settings.JT_IDENTITY_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as exc:
        logger.warning("jt.client_search.network_error err=%s", exc.__class__.__name__)
        return _search_failed()

    if response.status_code != 200:
        logger.warning("jt.client_search.non_200 status=%s", response.status_code)
        return _search_failed()

    try:
        payload = response.json()
    except ValueError:
        logger.warning("jt.client_search.bad_json")
        return _search_failed()

    rows = payload.get("clients") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        logger.warning("jt.client_search.bad_envelope")
        return _search_failed()

    return ClientSearchResult(failed=False, clients=[r for r in rows if isinstance(r, dict)])
