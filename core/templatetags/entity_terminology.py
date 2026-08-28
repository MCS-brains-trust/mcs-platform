from django import template

from core.entity_terminology import beneficiary_noun as _beneficiary_noun

register = template.Library()


@register.filter
def beneficiary_noun(entity, plural=False):
    """Template-side wrapper for core.entity_terminology.beneficiary_noun.

    Usage: {{ entity|beneficiary_noun }} -> "Beneficiary" / "Unit Holder"
           {{ entity|beneficiary_noun:True }} -> plural form
    """
    return _beneficiary_noun(entity, plural=plural)
