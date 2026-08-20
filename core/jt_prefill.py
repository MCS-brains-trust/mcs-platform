"""Turn JT's identity envelope into EntityForm initial values.

Pure mapping, no I/O: the caller fetches, this decides. A field appears in the
result only when JT actually holds it, so a missing key means "operator types
it" rather than "overwrite with blank" -- the difference matters on the edit
form, where prefill must never clear a value someone entered by hand.

TFN is deliberately absent. JT masks it to `restricted` and StatementHub never
sends includePii=true, but the refusal is written here as well so a future
caller that does send it cannot leak a TFN into a form by accident.
"""
import re

from accounts.models import User
from core.models import Entity

# JT field -> SH form field, for the values that need no transformation.
SIMPLE_FIELDS = {
    "legalName": "entity_name",
    "address": "address_line_1",
    "city": "suburb",
    "region": "state",
    "postCode": "postcode",
    "country": "country",
    "email": "contact_email",
}


def _held(fields, name):
    """The value of a `held` field, trimmed, or None for any absence."""
    entry = fields.get(name)
    if not isinstance(entry, dict) or entry.get("status") != "held":
        return None
    value = str(entry.get("value") or "").strip()
    return value or None


def _normalise(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _entity_type(label):
    """XPM's entity-type wording -> one of SH's five stored values.

    Matched on both the stored value and the human label, so "Sole Trader" and
    "sole_trader" both land. Anything unrecognised returns None: a wrong entity
    type silently changes which statements SH generates, so it is left for the
    operator rather than guessed.
    """
    key = _normalise(label)
    for value, display in Entity.EntityType.choices:
        if key in (_normalise(value), _normalise(display)):
            return value
    return None


def _accountant_pk(full_name):
    """The active SH user whose full name is JT's account manager, if exactly one."""
    target = _normalise(full_name)
    matches = [
        user.pk for user in User.objects.filter(is_active=True)
        if _normalise(user.get_full_name()) == target
    ]
    return matches[0] if len(matches) == 1 else None


def prefill_from_identity(fields):
    """Initial values for EntityForm, given JT's typed-absence envelope."""
    initial = {}

    for jt_name, form_field in SIMPLE_FIELDS.items():
        value = _held(fields, jt_name)
        if value:
            initial[form_field] = value

    abn = _held(fields, "abn")
    if abn:
        # XPM stores ABNs formatted ("12 345 678 901") and Entity.abn is 11
        # characters, so the digits are the only form that saves.
        digits = re.sub(r"\D", "", abn)
        if digits:
            initial["abn"] = digits

    entity_type = _held(fields, "entityType")
    if entity_type:
        mapped = _entity_type(entity_type)
        if mapped:
            initial["entity_type"] = mapped

    manager = _held(fields, "accountManager")
    if manager:
        pk = _accountant_pk(manager)
        if pk:
            initial["assigned_accountant"] = pk

    return initial
