"""The backfill for names already stored in capitals.

normalise_person_name runs on save(), so a record nobody saves keeps its
capitals indefinitely. Production had ELLIOTT JAQUES sitting on both
entity_name and trading_as of a sole trader.

Fixtures here write with queryset.update() rather than save(), which is the
only way to get an un-normalised row past the very hook being tested.
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from core.models import Client, Entity, EntityOfficer


class NormalisePersonNamesCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="Backfill Client")
        Client.objects.filter(pk=cls.client_obj.pk).update(name="BACKFILL CLIENT")

        cls.sole = Entity.objects.create(
            entity_name="Placeholder", entity_type="sole_trader", client=cls.client_obj,
        )
        Entity.objects.filter(pk=cls.sole.pk).update(
            entity_name="ELLIOTT JAQUES", trading_as="ELLIOTT JAQUES",
        )

        cls.company = Entity.objects.create(
            entity_name="ABC PTY LTD", entity_type="company", client=cls.client_obj,
        )

        cls.officer = EntityOfficer.objects.create(
            entity=cls.company, full_name="Placeholder", role="director",
        )
        EntityOfficer.objects.filter(pk=cls.officer.pk).update(full_name="JANE O'BRIEN")

    def _run(self, *args):
        out = StringIO()
        call_command("normalise_person_names", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_reports_without_changing_anything(self):
        output = self._run("--dry-run")

        self.assertIn("ELLIOTT JAQUES", output)
        self.assertIn("Elliott Jaques", output)
        self.sole.refresh_from_db()
        self.assertEqual(self.sole.entity_name, "ELLIOTT JAQUES")

    def test_it_normalises_every_person_name_field(self):
        self._run()

        self.sole.refresh_from_db()
        self.assertEqual(self.sole.entity_name, "Elliott Jaques")
        self.assertEqual(self.sole.trading_as, "Elliott Jaques")

        self.client_obj.refresh_from_db()
        self.assertEqual(self.client_obj.name, "Backfill Client")

        self.officer.refresh_from_db()
        self.assertEqual(self.officer.full_name, "Jane O'Brien")

    def test_a_company_name_is_left_alone(self):
        self._run()

        self.company.refresh_from_db()
        self.assertEqual(self.company.entity_name, "ABC PTY LTD")

    def test_it_is_safe_to_run_twice(self):
        self._run()
        second = self._run()

        self.assertIn("0", second)
        self.sole.refresh_from_db()
        self.assertEqual(self.sole.entity_name, "Elliott Jaques")
