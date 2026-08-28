"""What this entity calls the people who receive its income."""


def beneficiary_noun(entity, plural=False):
    """"Unit Holder" for a unit trust, "Beneficiary" otherwise.

    A unit trust's income recipients are unit holders — the word matters on
    financial statements and distribution minutes, not only on screen.
    """
    if getattr(entity, "is_unit_trust", False):
        return "Unit Holders" if plural else "Unit Holder"
    return "Beneficiaries" if plural else "Beneficiary"
