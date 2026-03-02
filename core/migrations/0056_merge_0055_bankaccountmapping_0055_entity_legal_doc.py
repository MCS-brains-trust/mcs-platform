"""
Merge migration to resolve conflict between:
  - 0055_bankaccountmapping
  - 0055_entity_legal_doc_prompt_dismissed

Both depend on 0054_add_company_establishment but were created independently.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0055_bankaccountmapping"),
        ("core", "0055_entity_legal_doc_prompt_dismissed"),
    ]

    operations = []
