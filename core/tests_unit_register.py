"""Units are the register; the percentage is derived from them."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from core.forms import EntityOfficerForm
from core.models import Entity, EntityOfficer, OfficerDistributionHistory
from core.views import _handle_ceased_redistribution


def _prepare_request(request, user):
    """Same pattern as integrations/tests_import_wizard_bugs.py: a view
    helper that calls django.contrib.messages needs a request with a real
    session and message storage attached, or messages.info()/warning()
    raise MessageFailure."""
    request.user = user
    SessionMiddleware(lambda req: None).process_request(request)
    request.session.save()
    request._messages = FallbackStorage(request)
    return request


class UnitRegisterTests(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
        )

    def _holder(self, name, units):
        return EntityOfficer.objects.create(
            entity=self.entity, full_name=name,
            role="unit_holder", roles=["unit_holder"], units_held=units,
        )

    def test_total_units_sums_active_holders(self):
        self._holder("Double Water International Pty Ltd", 50)
        self._holder("Penman Property Nominees Pty Ltd", 50)
        self.assertEqual(self.entity.total_units, 100)

    def test_percentage_is_derived_from_units(self):
        a = self._holder("Double Water International Pty Ltd", 50)
        b = self._holder("Penman Property Nominees Pty Ltd", 50)
        self.assertEqual(a.unit_percentage, Decimal("50.0000"))
        self.assertEqual(b.unit_percentage, Decimal("50.0000"))

    def test_uneven_split_derives_exactly(self):
        a = self._holder("A", 1)
        self._holder("B", 2)
        self.assertEqual(a.unit_percentage, Decimal("33.3333"))

    def test_distribution_percentage_is_stored_from_units_on_save(self):
        # Stored, not a pure property: existing consumers read this field.
        #
        # unit_percentage divides by entity.total_units, a live DB
        # aggregate. A is saved before B exists, so A's stored percentage
        # is computed against a total of just A's units (100%). Adding B
        # afterwards does not retroactively fix A's stored value -- only
        # recalculate_unit_percentages() rewrites every holder from the
        # final register. So we call it explicitly, rather than asserting
        # a value that only holds by luck of insertion order.
        a = self._holder("A", 75)
        self._holder("B", 25)
        EntityOfficer.recalculate_unit_percentages(self.entity)
        a.refresh_from_db()
        self.assertEqual(a.distribution_percentage, Decimal("75.00"))

    def test_percentage_is_zero_when_no_units_on_issue(self):
        a = EntityOfficer.objects.create(
            entity=self.entity, full_name="A",
            role="unit_holder", roles=["unit_holder"],
        )
        self.assertEqual(a.unit_percentage, Decimal("0"))

    def test_non_unit_holders_may_not_hold_units(self):
        officer = EntityOfficer(
            entity=self.entity, full_name="T", role="trustee", units_held=10,
        )
        officer.clean()
        self.assertIsNone(officer.units_held)

    def test_ceased_holders_are_excluded_from_total(self):
        self._holder("A", 50)
        ceased = self._holder("B", 50)
        ceased.date_ceased = date(2025, 1, 1)
        ceased.save()
        self.assertEqual(self.entity.total_units, 50)

    def test_discretionary_trust_beneficiary_unaffected(self):
        """A beneficiary of a discretionary trust must be untouched by any
        of this: distribution_percentage stays freely typed and units_held
        stays null."""
        disc_trust = Entity.objects.create(
            entity_name="Ordinary Family Trust", entity_type="trust",
        )
        beneficiary = EntityOfficer.objects.create(
            entity=disc_trust, full_name="Jane Beneficiary",
            role="beneficiary", roles=["beneficiary"],
            distribution_percentage=Decimal("40.00"),
        )
        beneficiary.refresh_from_db()
        # The typed value is preserved exactly -- not overwritten by any
        # unit-derived computation, because units_held was never set.
        self.assertEqual(beneficiary.distribution_percentage, Decimal("40.00"))
        self.assertIsNone(beneficiary.units_held)
        # clean() must not touch units_held for a beneficiary either.
        beneficiary.clean()
        self.assertIsNone(beneficiary.units_held)


class UnitRegisterFixRoundTests(TestCase):
    """Fix-round tests: each one corresponds to a defect the reviewer found
    empirically and every test above passed straight through."""

    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
        )

    def _holder(self, name, units):
        return EntityOfficer.objects.create(
            entity=self.entity, full_name=name,
            role="unit_holder", roles=["unit_holder"], units_held=units,
        )

    # -- FIX 5: clean()'s units guard must narrow on `role` only, not widen
    # to match the `roles`-aware distribution_percentage guard. -----------

    def test_clean_nulls_units_for_trustee_who_also_lists_unit_holder_role(self):
        # role="trustee" with "unit_holder" in the roles list must still
        # lose units_held: the units guard checks `role` only, mirroring
        # what actually assigns role="unit_holder" (core/forms.py).
        officer = EntityOfficer(
            entity=self.entity, full_name="T",
            role="trustee", roles=["trustee", "unit_holder"], units_held=10,
        )
        officer.clean()
        self.assertIsNone(officer.units_held)
        # And the pre-existing distribution_percentage guard (role-only)
        # keeps behaving the same way: nulled too.
        officer.distribution_percentage = Decimal("50.00")
        officer.clean()
        self.assertIsNone(officer.distribution_percentage)
        # Pin the FIX 5 (round 1) resurrection bug: clean() having just
        # nulled the percentage must not be undone by save(). Before that
        # fix, save()'s old per-instance recompute put it back from units
        # whenever units_held survived clean() (it no longer does here,
        # but this asserts the actual persisted outcome, not just the
        # in-memory state clean() leaves behind).
        officer.save()
        officer.refresh_from_db()
        self.assertIsNone(officer.distribution_percentage)
        self.assertIsNone(officer.units_held)

    def test_clean_nulls_units_for_plain_trustee(self):
        # roles=["trustee"] alone (no "unit_holder" anywhere): still
        # nulled. Proves deleting the old roles-list clause changes
        # nothing observable -- the role-only check already covers this.
        officer = EntityOfficer(
            entity=self.entity, full_name="T",
            role="trustee", roles=["trustee"], units_held=10,
        )
        officer.clean()
        self.assertIsNone(officer.units_held)

    # -- FIX 1: no wrong insert-time percentage, no inverted-date history --

    def test_recalculate_writes_correct_history_with_no_inverted_dates(self):
        a = self._holder("A", 75)
        b = self._holder("B", 25)
        EntityOfficer.recalculate_unit_percentages(self.entity)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.distribution_percentage, Decimal("75.00"))
        self.assertEqual(b.distribution_percentage, Decimal("25.00"))

        a_history = list(
            OfficerDistributionHistory.objects.filter(officer=a).order_by("effective_from")
        )
        # Exactly one row, correct percentage, no wrong intermediate 0.00%
        # or 100% row ever booked, and no inverted (effective_to before
        # effective_from) period.
        self.assertEqual(len(a_history), 1)
        self.assertEqual(a_history[0].distribution_pct, Decimal("75.00"))
        for row in a_history:
            if row.effective_to is not None:
                self.assertGreaterEqual(row.effective_to, row.effective_from)

        b_history = list(OfficerDistributionHistory.objects.filter(officer=b))
        self.assertEqual(len(b_history), 1)
        self.assertEqual(b_history[0].distribution_pct, Decimal("25.00"))

    def test_individual_save_does_not_book_wrong_percentage_or_history(self):
        # A unit holder's own save() must never derive distribution_
        # percentage from units_held (only recalculate_unit_percentages
        # does) and must never write history for it either -- both are
        # left for the batch recompute, which alone sees the true total.
        a = self._holder("A", 75)
        a.refresh_from_db()
        self.assertIsNone(a.distribution_percentage)
        self.assertEqual(OfficerDistributionHistory.objects.filter(officer=a).count(), 0)

    # -- FIX 2: ceased holders must not blow up / overflow the recompute --

    def test_recalculate_excludes_ceased_holder_and_does_not_overflow(self):
        ceased = self._holder("Ceased", 9000)
        ceased.date_ceased = date(2020, 1, 1)
        ceased.save()
        active = self._holder("Active", 1)
        # Must not raise decimal.InvalidOperation / DataError.
        EntityOfficer.recalculate_unit_percentages(self.entity)
        active.refresh_from_db()
        ceased.refresh_from_db()
        self.assertEqual(active.distribution_percentage, Decimal("100.00"))
        # Ceased holder's percentage is frozen (untouched), not nulled and
        # not recomputed against the active-only total.
        self.assertIsNone(ceased.distribution_percentage)

    # -- FIX 3: total_units must not disenfranchise a future-ceased holder -

    def test_total_units_includes_future_ceased_holder(self):
        self._holder("Leaving later", 50)
        self._holder("Staying", 50)
        leaving = EntityOfficer.objects.get(full_name="Leaving later")
        leaving.date_ceased = timezone.now().date() + timedelta(days=30)
        leaving.save()
        self.assertEqual(self.entity.total_units, 100)
        EntityOfficer.recalculate_unit_percentages(self.entity)
        leaving.refresh_from_db()
        self.assertEqual(leaving.distribution_percentage, Decimal("50.00"))

    # -- FIX 4: stored percentages must sum to exactly 100.00 --------------

    def test_three_equal_holders_sum_to_exactly_100(self):
        self._holder("A", 1)
        self._holder("B", 1)
        self._holder("C", 1)
        EntityOfficer.recalculate_unit_percentages(self.entity)
        total = sum(
            (o.distribution_percentage for o in EntityOfficer.objects.filter(entity=self.entity)),
            Decimal("0"),
        )
        self.assertEqual(total, Decimal("100.00"))


class BeneficiaryDistributionHistoryTests(TestCase):
    """FIX 4 (round 2): _write_distribution_history's behaviour change for
    BENEFICIARIES -- the primary risk path, with four discretionary trusts
    in production -- was previously untested."""

    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Ordinary Family Trust", entity_type="trust",
        )

    def _all_rows_never_invert(self, officer):
        for row in OfficerDistributionHistory.objects.filter(officer=officer):
            if row.effective_to is not None:
                self.assertGreaterEqual(
                    row.effective_to, row.effective_from,
                    f"row {row.pk} has effective_to before effective_from",
                )

    def test_same_day_edits_collapse_into_one_open_row(self):
        beneficiary = EntityOfficer.objects.create(
            entity=self.entity, full_name="Jane Beneficiary",
            role="beneficiary", roles=["beneficiary"],
            distribution_percentage=Decimal("40.00"),
        )
        beneficiary.distribution_percentage = Decimal("60.00")
        beneficiary.save()
        beneficiary.distribution_percentage = Decimal("70.00")
        beneficiary.save()

        rows = list(OfficerDistributionHistory.objects.filter(officer=beneficiary))
        open_rows = [r for r in rows if r.effective_to is None]
        # Exactly one open row survives three same-day writes, not three
        # rows or an inverted-date row per edit.
        self.assertEqual(len(open_rows), 1)
        self.assertEqual(open_rows[0].distribution_pct, Decimal("70.00"))
        self._all_rows_never_invert(beneficiary)

    def test_multi_day_edit_still_produces_two_row_shape(self):
        beneficiary = EntityOfficer.objects.create(
            entity=self.entity, full_name="Jane Beneficiary",
            role="beneficiary", roles=["beneficiary"],
            distribution_percentage=Decimal("40.00"),
        )
        today = timezone.now().date()
        yesterday = today - timedelta(days=1)
        # Simulate the existing open row having been opened on an earlier
        # day (as if the officer had been created yesterday).
        OfficerDistributionHistory.objects.filter(officer=beneficiary).update(
            effective_from=yesterday,
        )

        beneficiary.distribution_percentage = Decimal("55.00")
        beneficiary.save()

        rows = list(
            OfficerDistributionHistory.objects.filter(officer=beneficiary).order_by("effective_from")
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].effective_from, yesterday)
        self.assertEqual(rows[0].effective_to, yesterday)
        self.assertEqual(rows[0].distribution_pct, Decimal("40.00"))
        self.assertEqual(rows[1].effective_from, today)
        self.assertIsNone(rows[1].effective_to)
        self.assertEqual(rows[1].distribution_pct, Decimal("55.00"))
        self._all_rows_never_invert(beneficiary)

    def test_stray_second_open_row_is_closed_alongside_the_newest(self):
        # FIX 2 (round 2): a second open row (a data anomaly that should
        # never occur going forward, but could pre-exist) must not be left
        # open forever once a save() writes a new history entry.
        beneficiary = EntityOfficer.objects.create(
            entity=self.entity, full_name="Jane Beneficiary",
            role="beneficiary", roles=["beneficiary"],
            distribution_percentage=Decimal("40.00"),
        )
        stray = OfficerDistributionHistory.objects.create(
            officer=beneficiary, distribution_pct=Decimal("40.00"),
            effective_from=timezone.now().date() - timedelta(days=5),
        )
        beneficiary.distribution_percentage = Decimal("60.00")
        beneficiary.save()

        stray.refresh_from_db()
        self.assertIsNotNone(stray.effective_to)
        open_rows = OfficerDistributionHistory.objects.filter(
            officer=beneficiary, effective_to__isnull=True,
        )
        self.assertEqual(open_rows.count(), 1)
        self.assertEqual(open_rows.first().distribution_pct, Decimal("60.00"))
        self._all_rows_never_invert(beneficiary)


class CeasedRedistributionTests(TestCase):
    """FIX 1 (round 3, the important one): the unit-trust branch of
    core/views.py's _handle_ceased_redistribution has no committed test.
    Its evidence in fix round 2 was a one-off `manage.py shell` probe, not
    part of the suite -- and that exact regression (a hand-set stored
    field with no matching history row) was introduced once already
    inside this task. This pins it so a future edit to that branch cannot
    silently reintroduce it with the suite still green.

    _handle_ceased_redistribution is called directly rather than driven
    through the view: it needs no entity/session state beyond a request
    that can carry django.contrib.messages, and driving the full
    entity_officer_edit view would require IDOR-check scaffolding
    (get_entity_for_user), a bound ModelForm, and login_required/2FA
    middleware unrelated to what this is testing.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="redistrib", email="redistrib@example.com", password="secret123",
        )

    def _request(self):
        return _prepare_request(self.factory.post("/fake-officer-edit/"), self.user)

    def _all_rows_never_invert(self, officer):
        for row in OfficerDistributionHistory.objects.filter(officer=officer):
            if row.effective_to is not None:
                self.assertGreaterEqual(
                    row.effective_to, row.effective_from,
                    f"row {row.pk} has effective_to before effective_from",
                )

    def test_unit_trust_survivor_stored_field_and_open_history_agree(self):
        entity = Entity.objects.create(
            entity_name="Redistribution Unit Trust", entity_type="trust_unit",
        )
        a = EntityOfficer.objects.create(
            entity=entity, full_name="A", role="unit_holder", roles=["unit_holder"],
            units_held=50,
        )
        b = EntityOfficer.objects.create(
            entity=entity, full_name="B", role="unit_holder", roles=["unit_holder"],
            units_held=50,
        )
        EntityOfficer.recalculate_unit_percentages(entity)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.distribution_percentage, Decimal("50.00"))
        self.assertEqual(b.distribution_percentage, Decimal("50.00"))

        # Cease B through the same path entity_officer_edit takes: set
        # date_ceased, save, then call the redistribution handler.
        b.date_ceased = timezone.now().date()
        b.save()
        _handle_ceased_redistribution(self._request(), b)

        a.refresh_from_db()
        self.assertEqual(a.distribution_percentage, Decimal("100.00"))
        open_row = OfficerDistributionHistory.objects.filter(
            officer=a, effective_to__isnull=True,
        ).first()
        self.assertIsNotNone(open_row)
        # This is the assertion that fails if the round-2 regression
        # returns: a hand-set stored field with no matching history write.
        self.assertEqual(open_row.distribution_pct, Decimal("100.00"))
        self._all_rows_never_invert(a)
        self._all_rows_never_invert(b)

    def test_discretionary_survivor_stored_field_and_open_history_agree(self):
        entity = Entity.objects.create(
            entity_name="Redistribution Family Trust", entity_type="trust",
        )
        a = EntityOfficer.objects.create(
            entity=entity, full_name="A", role="beneficiary", roles=["beneficiary"],
            distribution_percentage=Decimal("50.00"),
        )
        b = EntityOfficer.objects.create(
            entity=entity, full_name="B", role="beneficiary", roles=["beneficiary"],
            distribution_percentage=Decimal("50.00"),
        )
        b.date_ceased = timezone.now().date()
        b.save()
        _handle_ceased_redistribution(self._request(), b)

        a.refresh_from_db()
        self.assertEqual(a.distribution_percentage, Decimal("100.00"))
        open_row = OfficerDistributionHistory.objects.filter(
            officer=a, effective_to__isnull=True,
        ).first()
        self.assertIsNotNone(open_row)
        self.assertEqual(open_row.distribution_pct, Decimal("100.00"))
        self._all_rows_never_invert(a)
        self._all_rows_never_invert(b)


class UnitHolderRoleGuardTests(TestCase):
    """FIX 3 (round 3): fix 3's own bug scenario -- a role="beneficiary"
    officer with units_held forced through direct ORM previously got NO
    history at all -- was itself untested."""

    def test_beneficiary_with_units_held_forced_via_orm_still_gets_history(self):
        entity = Entity.objects.create(
            entity_name="Ordinary Family Trust", entity_type="trust",
        )
        beneficiary = EntityOfficer.objects.create(
            entity=entity, full_name="Jane Beneficiary",
            role="beneficiary", roles=["beneficiary"],
            distribution_percentage=Decimal("40.00"),
        )
        # Force units_held directly through the ORM, bypassing clean()
        # (which would otherwise null it for a non-unit-holder role).
        EntityOfficer.objects.filter(pk=beneficiary.pk).update(units_held=10)
        beneficiary.refresh_from_db()
        self.assertEqual(beneficiary.units_held, 10)

        beneficiary.distribution_percentage = Decimal("55.00")
        beneficiary.save()

        open_row = OfficerDistributionHistory.objects.filter(
            officer=beneficiary, effective_to__isnull=True,
        ).first()
        # Under the old units_held-keyed guard, this would be None: the
        # save() above would have been silently dropped from the audit
        # trail entirely (neither the unit-holder path nor the
        # beneficiary path would write it).
        self.assertIsNotNone(open_row)
        self.assertEqual(open_row.distribution_pct, Decimal("55.00"))


class UnitRegisterFormTests(TestCase):
    """Task 6 brief's own test, plus form-level assertions for the
    units_held / distribution_percentage show-hide-readonly wiring."""

    def test_saving_a_holder_recalculates_every_percentage(self):
        entity = Entity.objects.create(entity_name="Minli", entity_type="trust_unit")
        a = EntityOfficer.objects.create(
            entity=entity, full_name="A", role="unit_holder",
            roles=["unit_holder"], units_held=50,
        )
        EntityOfficer.objects.create(
            entity=entity, full_name="B", role="unit_holder",
            roles=["unit_holder"], units_held=50,
        )
        EntityOfficer.recalculate_unit_percentages(entity)

        a.refresh_from_db()
        self.assertEqual(a.distribution_percentage, Decimal("50.00"))

    def test_form_shows_units_held_for_unit_trust(self):
        form = EntityOfficerForm(entity_type="trust_unit")
        self.assertNotIsInstance(form.fields["units_held"].widget, forms.HiddenInput)
        # distribution_percentage must be visible (not hidden) and disabled
        # (read-only, immune to a tampered POST) for a unit trust.
        self.assertNotIsInstance(
            form.fields["distribution_percentage"].widget, forms.HiddenInput
        )
        self.assertTrue(form.fields["distribution_percentage"].disabled)

    def test_form_hides_units_held_for_discretionary_trust(self):
        form = EntityOfficerForm(entity_type="trust")
        self.assertIsInstance(form.fields["units_held"].widget, forms.HiddenInput)
        # A discretionary trust's distribution_percentage stays exactly as
        # Task 4 left it: visible and freely typed, not disabled.
        self.assertNotIsInstance(
            form.fields["distribution_percentage"].widget, forms.HiddenInput
        )
        self.assertFalse(form.fields["distribution_percentage"].disabled)

    def test_form_hides_distribution_percentage_for_company(self):
        # Unrelated entity types must be completely unaffected.
        form = EntityOfficerForm(entity_type="company")
        self.assertIsInstance(
            form.fields["distribution_percentage"].widget, forms.HiddenInput
        )
        self.assertIsInstance(form.fields["units_held"].widget, forms.HiddenInput)


class UnitRegisterViewWiringTests(TestCase):
    """Proves recalculate_unit_percentages is actually wired into the
    officer views -- create, edit and delete -- not just callable from a
    test that invokes it directly. These fail today: nothing outside the
    tests calls it, so a unit holder saved through the view ends with
    distribution_percentage = None forever.
    """

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="unitreg", email="unitreg@example.com", password="secret123",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-unitreg", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()
        self.entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
        )

    def _create(self, full_name, units_held, display_order=1):
        return self.client.post(
            reverse("core:entity_officer_create", args=[self.entity.pk]),
            data={
                "full_name": full_name,
                "roles_multi": ["unit_holder"],
                "title": "",
                "date_appointed": "",
                "date_ceased": "",
                "display_order": str(display_order),
                "profit_share_percentage": "",
                "distribution_percentage": "",
                "units_held": str(units_held),
            },
            secure=True,
        )

    def test_creating_a_holder_through_the_view_leaves_correct_stored_percentage(self):
        response = self._create("A", 50)
        self.assertEqual(response.status_code, 302)
        a = EntityOfficer.objects.get(entity=self.entity, full_name="A")
        # Sole holder: recalculate_unit_percentages must have run, storing
        # 100.00%, not leaving distribution_percentage as None (what
        # happens today with no view wiring at all).
        self.assertEqual(a.distribution_percentage, Decimal("100.00"))

    def test_creating_a_second_holder_corrects_the_first_one_too(self):
        self._create("A", 50, display_order=1)
        a = EntityOfficer.objects.get(entity=self.entity, full_name="A")
        self.assertEqual(a.distribution_percentage, Decimal("100.00"))

        response = self._create("B", 50, display_order=2)
        self.assertEqual(response.status_code, 302)

        a.refresh_from_db()
        b = EntityOfficer.objects.get(entity=self.entity, full_name="B")
        self.assertEqual(a.distribution_percentage, Decimal("50.00"))
        self.assertEqual(b.distribution_percentage, Decimal("50.00"))

    def test_deleting_a_holder_recomputes_the_remainder(self):
        self._create("A", 50, display_order=1)
        self._create("B", 25, display_order=2)
        c_resp = self._create("C", 25, display_order=3)
        self.assertEqual(c_resp.status_code, 302)

        a = EntityOfficer.objects.get(entity=self.entity, full_name="A")
        b = EntityOfficer.objects.get(entity=self.entity, full_name="B")
        c = EntityOfficer.objects.get(entity=self.entity, full_name="C")
        self.assertEqual(a.distribution_percentage, Decimal("50.00"))

        response = self.client.post(
            reverse("core:entity_officer_delete", args=[c.pk]), secure=True,
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(EntityOfficer.objects.filter(pk=c.pk).exists())

        a.refresh_from_db()
        b.refresh_from_db()
        # Units were 50/25 out of 100 before C left; with C gone, the
        # remaining 50/25 units are the whole 75 -- recompute must sum to
        # exactly 100.00 over the two survivors.
        total = a.distribution_percentage + b.distribution_percentage
        self.assertEqual(total, Decimal("100.00"))
        self.assertEqual(a.distribution_percentage, Decimal("66.67"))
        self.assertEqual(b.distribution_percentage, Decimal("33.33"))

    def test_editing_a_holders_units_recomputes_every_holder(self):
        self._create("A", 50, display_order=1)
        self._create("B", 50, display_order=2)
        a = EntityOfficer.objects.get(entity=self.entity, full_name="A")
        b = EntityOfficer.objects.get(entity=self.entity, full_name="B")
        self.assertEqual(a.distribution_percentage, Decimal("50.00"))

        response = self.client.post(
            reverse("core:entity_officer_edit", args=[a.pk]),
            data={
                "full_name": "A",
                "roles_multi": ["unit_holder"],
                "title": "",
                "date_appointed": "",
                "date_ceased": "",
                "display_order": "1",
                "profit_share_percentage": "",
                "distribution_percentage": "",
                "units_held": "150",
            },
            secure=True,
        )
        self.assertEqual(response.status_code, 302)

        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.distribution_percentage, Decimal("75.00"))
        self.assertEqual(b.distribution_percentage, Decimal("25.00"))


class DiscretionaryTrustOfficerSaveRegressionTests(TestCase):
    """PRIMARY RISK guard: four discretionary trusts exist in production.
    A discretionary trust's officer form must be completely unchanged by
    any of this task's wiring -- distribution_percentage still freely
    typed, no units field, no recompute call on its save path.
    """

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="discreg", email="discreg@example.com", password="secret123",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-discreg", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()
        self.entity = Entity.objects.create(
            entity_name="Ordinary Family Trust", entity_type="trust",
        )

    def test_discretionary_trust_officer_save_still_persists_typed_percentage(self):
        response = self.client.post(
            reverse("core:entity_officer_create", args=[self.entity.pk]),
            data={
                "full_name": "Jane Beneficiary",
                "roles_multi": ["beneficiary"],
                "title": "",
                "date_appointed": "",
                "date_ceased": "",
                "display_order": "1",
                "profit_share_percentage": "",
                "distribution_percentage": "40.00",
                "units_held": "",
            },
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        jane = EntityOfficer.objects.get(entity=self.entity, full_name="Jane Beneficiary")
        # The hand-typed value survives exactly -- no units-derived
        # recompute has touched it, because this entity is not a unit
        # trust.
        self.assertEqual(jane.distribution_percentage, Decimal("40.00"))
        self.assertIsNone(jane.units_held)

    def test_editing_a_typed_percentage_on_a_discretionary_trust_still_works(self):
        beneficiary = EntityOfficer.objects.create(
            entity=self.entity, full_name="Jane Beneficiary",
            role="beneficiary", roles=["beneficiary"],
            distribution_percentage=Decimal("40.00"),
        )
        response = self.client.post(
            reverse("core:entity_officer_edit", args=[beneficiary.pk]),
            data={
                "full_name": "Jane Beneficiary",
                "roles_multi": ["beneficiary"],
                "title": "",
                "date_appointed": "",
                "date_ceased": "",
                "display_order": "1",
                "profit_share_percentage": "",
                "distribution_percentage": "65.00",
                "units_held": "",
            },
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        beneficiary.refresh_from_db()
        self.assertEqual(beneficiary.distribution_percentage, Decimal("65.00"))

    def test_recompute_is_not_called_at_all_for_a_discretionary_trust_create(self):
        # Fix round 1, FIX 2: the two tests above only assert the OUTCOME
        # (typed value persists), and recalculate_unit_percentages()
        # early-returns on a register with no unit_holder rows regardless
        # of whether it is called -- so deleting the `if
        # entity.is_unit_trust:` guard from the view left both of those
        # tests green. Assert the guard itself: the recompute must never
        # even be invoked for a discretionary trust.
        with patch.object(EntityOfficer, "recalculate_unit_percentages") as mock_recalc:
            response = self.client.post(
                reverse("core:entity_officer_create", args=[self.entity.pk]),
                data={
                    "full_name": "Jane Beneficiary",
                    "roles_multi": ["beneficiary"],
                    "title": "",
                    "date_appointed": "",
                    "date_ceased": "",
                    "display_order": "1",
                    "profit_share_percentage": "",
                    "distribution_percentage": "40.00",
                    "units_held": "",
                },
                secure=True,
            )
        self.assertEqual(response.status_code, 302)
        mock_recalc.assert_not_called()

    def test_recompute_is_not_called_at_all_for_a_discretionary_trust_edit(self):
        beneficiary = EntityOfficer.objects.create(
            entity=self.entity, full_name="Jane Beneficiary",
            role="beneficiary", roles=["beneficiary"],
            distribution_percentage=Decimal("40.00"),
        )
        with patch.object(EntityOfficer, "recalculate_unit_percentages") as mock_recalc:
            response = self.client.post(
                reverse("core:entity_officer_edit", args=[beneficiary.pk]),
                data={
                    "full_name": "Jane Beneficiary",
                    "roles_multi": ["beneficiary"],
                    "title": "",
                    "date_appointed": "",
                    "date_ceased": "",
                    "display_order": "1",
                    "profit_share_percentage": "",
                    "distribution_percentage": "65.00",
                    "units_held": "",
                },
                secure=True,
            )
        self.assertEqual(response.status_code, 302)
        mock_recalc.assert_not_called()


class OfficersListAndFormRenderingTests(TestCase):
    """Fix round 1, FIX 4: the Units column, its colspan bump, and the
    unit-trust-only "Derived from units held." helper text had no
    rendering test at all -- coverage stopped at the form-field level.
    """

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="listrender", email="listrender@example.com", password="secret123",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-listrender", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

    def test_unit_trust_officers_list_shows_units_column_and_wider_colspan(self):
        entity = Entity.objects.create(entity_name="Minli", entity_type="trust_unit")
        EntityOfficer.objects.create(
            entity=entity, full_name="A", role="unit_holder",
            roles=["unit_holder"], units_held=100,
        )
        response = self.client.get(
            reverse("core:entity_officers", args=[entity.pk]), secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<th>Units</th>", html=True)
        self.assertContains(response, 'colspan="9"')

    def test_discretionary_trust_officers_list_hides_units_column_and_colspan_unchanged(self):
        entity = Entity.objects.create(entity_name="Ordinary Family Trust", entity_type="trust")
        EntityOfficer.objects.create(
            entity=entity, full_name="Jane", role="beneficiary",
            roles=["beneficiary"], distribution_percentage=Decimal("100.00"),
        )
        response = self.client.get(
            reverse("core:entity_officers", args=[entity.pk]), secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<th>Units</th>", html=True)
        self.assertContains(response, 'colspan="8"')
        self.assertNotContains(response, 'colspan="9"')

    def test_unit_trust_officer_form_shows_derived_helper_text(self):
        entity = Entity.objects.create(entity_name="Minli", entity_type="trust_unit")
        response = self.client.get(
            reverse("core:entity_officer_create", args=[entity.pk]), secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Derived from units held.")

    def test_discretionary_trust_officer_form_shows_original_helper_text(self):
        entity = Entity.objects.create(entity_name="Ordinary Family Trust", entity_type="trust")
        response = self.client.get(
            reverse("core:entity_officer_create", args=[entity.pk]), secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Percentage of trust distribution allocated to this beneficiary/unit holder",
        )
        self.assertNotContains(response, "Derived from units held.")
