# core/tests_entity_xpm_link.py
"""The XPM link column on Entity.

Indexed because every by-xpm resolution and every identity fetch filters on it.
NOT unique: one XPM client legitimately owns several entities here — a trading
company and the family trust behind it — which is precisely why the by-xpm
resolver has a chooser branch.
"""
from django.test import TestCase

from core.models import Entity

XPM_ID = "aaaaaaaa-0000-0000-0000-000000000009"


class EntityXpmLinkTests(TestCase):
    def test_column_is_indexed(self):
        indexed = {
            field_names[0]
            for field_names in [
                tuple(index.fields) for index in Entity._meta.indexes if index.fields
            ]
        }
        db_indexed = {f.name for f in Entity._meta.fields if getattr(f, "db_index", False)}
        self.assertTrue(
            "xpm_client_id" in indexed or "xpm_client_id" in db_indexed,
            "Entity.xpm_client_id must be indexed: every resolution filters on it",
        )

    def test_two_entities_may_share_one_xpm_client(self):
        Entity.objects.create(entity_name="Example Pty Ltd", entity_type="company",
                              xpm_client_id=XPM_ID)
        Entity.objects.create(entity_name="Example Family Trust", entity_type="trust",
                              xpm_client_id=XPM_ID)
        self.assertEqual(Entity.objects.filter(xpm_client_id=XPM_ID).count(), 2)

    def test_many_entities_may_be_unlinked(self):
        for i in range(3):
            Entity.objects.create(entity_name=f"Unlinked {i}", entity_type="company")
        self.assertEqual(Entity.objects.filter(xpm_client_id="").count(), 3)
