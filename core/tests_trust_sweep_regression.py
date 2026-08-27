"""The sweep must widen trust behaviour to unit trusts without altering trusts.

Guards Task 3 of the unit-trust plan: every "is this a trust?" behaviour branch
now tests membership in TRUST_LIKE_TYPES rather than equality with "trust", so
a unit trust gets the same behaviour a discretionary trust already had.

Three deliberate exceptions are pinned here rather than widened — see
test_section_100a_stays_discretionary_only,
test_eva_trust_planning_stays_dead_for_both_trust_kinds and
test_eva_compliance_checks_stay_dead_for_both_trust_kinds.

The reachability tests at the end exist because a byte-identical suite proves
the sweep broke nothing, but not that any widened behaviour actually reaches a
unit trust. Each one fails if its site is reverted.
"""
from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from core.models import (
    Client, Entity, EntityOfficer, FinancialYear, TRUST_LIKE_TYPES,
)
from core.beneficiary_account_service import _profile_for
from core.eva_engine import COMPLIANCE_CHECKS
from core.eva_service import ENTITY_CHECK_MAP
from core.eva_trust_planning import is_trust_planning_query
from core.fs_template_service import (
    build_trust_context, generate_financial_statements,
)
from core.risk_modules.section100a import Section100AModule
from integrations.xero_gl_summary import resolve_equity_code


STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STORAGES_OVERRIDE)
class TrustSweepRegressionTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(name="Trust Sweep Client")
        self.discretionary = Entity.objects.create(
            entity_name="Vincent Family Trust",
            entity_type="trust",
            client=self.client_obj,
        )
        self.unit = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust",
            entity_type="trust_unit",
            client=self.client_obj,
        )

    def test_trust_like_types_is_a_superset_of_trust(self):
        self.assertIn("trust", TRUST_LIKE_TYPES)
        self.assertIn("trust_unit", TRUST_LIKE_TYPES)

    def test_beneficiary_accounts_apply_to_both_trust_kinds(self):
        """core/beneficiary_account_service.py:_profile_for gates on entity
        type AND officer role together; a unit trust needs the same capital
        accounts a discretionary trust gets."""
        beneficiary = EntityOfficer.objects.create(
            entity=self.discretionary,
            full_name="Vincent Beneficiary",
            role=EntityOfficer.OfficerRole.BENEFICIARY,
        )
        unit_holder = EntityOfficer.objects.create(
            entity=self.unit,
            full_name="Minli Unit Holder",
            role=EntityOfficer.OfficerRole.UNIT_HOLDER,
        )
        self.assertIsNotNone(_profile_for(beneficiary))
        self.assertIsNotNone(_profile_for(unit_holder))

    def test_section_100a_stays_discretionary_only(self):
        """A fixed unit trust makes no discretionary distribution, so Section
        100A does not apply. This is a deliberate narrowing, not an omission."""
        fy_d = FinancialYear.objects.create(
            entity=self.discretionary,
            year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        fy_u = FinancialYear.objects.create(
            entity=self.unit,
            year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        self.assertTrue(Section100AModule(fy_d).should_run())
        self.assertFalse(Section100AModule(fy_u).should_run())
        self.assertEqual(Section100AModule.entity_types, ["trust"])

    def test_eva_trust_planning_stays_dead_for_both_trust_kinds(self):
        """Eva's chat trust-planning mode gates on three entity type values that
        have never existed in Entity.EntityType, so the feature has never run
        for any entity. "trust_unit" was deliberately removed from that gate:
        listing it would switch a never-run feature on for a live unit trust
        while the discretionary trusts still would not have it. Keeping it dead
        preserves the status quo for every entity. Not an oversight."""
        message = "who should we distribute the trust income to this year?"
        # The message itself must be one the gate would otherwise accept, or
        # this test would pass for the wrong reason.
        self.assertTrue(is_trust_planning_query(message, "trust_discretionary"))
        self.assertFalse(is_trust_planning_query(message, "trust_unit"))
        self.assertFalse(is_trust_planning_query(message, "trust"))

    def test_eva_compliance_checks_stay_dead_for_both_trust_kinds(self):
        """core/eva_engine.py's COMPLIANCE_CHECKS name "trust_discretionary"
        and "trust_hybrid", neither of which has ever existed in
        Entity.EntityType, so none of these LLM checks has ever run for any
        trust. "trust_unit" was deliberately removed: leaving it would switch
        nine never-run checks on for a single live unit trust while the
        discretionary trusts still had none. Deliberate, not an omission."""
        for check in COMPLIANCE_CHECKS:
            self.assertNotIn(
                "trust_unit", check["entity_types"],
                f'check {check["id"]} must not name trust_unit',
            )
            self.assertNotIn(
                "trust", check["entity_types"],
                f'check {check["id"]} must not name trust either — that would '
                f'activate a dead path on the four discretionary trusts',
            )
        # Reproduces the filter at core/eva_engine.py's _run_eva_review_background.
        for entity_type in TRUST_LIKE_TYPES:
            applicable = [
                c for c in COMPLIANCE_CHECKS if entity_type in c["entity_types"]
            ]
            self.assertEqual(applicable, [], f"{entity_type} must match no check")

    # ------------------------------------------------------------------
    # Reachability: the widened behaviour actually reaches a unit trust
    # ------------------------------------------------------------------

    def test_creating_a_unit_trust_seeds_its_chart_of_accounts(self):
        """core/signals.py's handle_trust_entity_created gated on the ENUM form
        (entity_type != Entity.EntityType.TRUST), which no string grep for
        == "trust" matches. Left narrow, a newly created unit trust would start
        life with ZERO chart accounts. Both entities here are created in setUp,
        so this asserts the post_save signal fired for both."""
        from core.models import EntityChartOfAccount

        for entity in (self.discretionary, self.unit):
            self.assertTrue(
                EntityChartOfAccount.objects.filter(entity=entity).exists(),
                f"{entity.entity_type} was created with no chart accounts",
            )

    def test_client_package_contents_reach_a_unit_trust(self):
        """Both PACKAGE_CONTENTS copies alias trust_unit to the trust entry.
        Aliased, not copied, so the two lists cannot drift. Without the key the
        consumers' .get(..., PACKAGE_CONTENTS["individual"]) would give a unit
        trust a cover letter and nothing else."""
        from core.package_service import PACKAGE_CONTENTS as SERVICE_CONTENTS
        from core.views_package_assembly import PACKAGE_CONTENTS as VIEW_CONTENTS

        self.assertIs(SERVICE_CONTENTS["trust_unit"], SERVICE_CONTENTS["trust"])
        self.assertIs(VIEW_CONTENTS["trust_unit"], VIEW_CONTENTS["trust"])

    def test_eva_entity_check_map_reaches_a_unit_trust(self):
        """core/eva_service.py's ENTITY_CHECK_MAP — the Eva *review* check list,
        not eva_engine's LLM checks — resolves for a unit trust. Without the key
        the consumer's .get(..., ["going_concern"]) would drop Division 7A,
        superannuation, ATO benchmarks, related party and TPAR."""
        self.assertIs(ENTITY_CHECK_MAP["trust_unit"], ENTITY_CHECK_MAP["trust"])
        self.assertIn("division_7a", ENTITY_CHECK_MAP["trust_unit"])

    def test_xero_equity_code_reaches_a_unit_trust(self):
        """A unit trust's retained earnings land in Undistributed income, not
        the company default of Retained earnings."""
        self.assertEqual(resolve_equity_code("trust_unit"), "BS-EQ-005")
        self.assertEqual(resolve_equity_code("trust"), "BS-EQ-005")
        self.assertEqual(resolve_equity_code("company"), "BS-EQ-002")

    def test_unit_trust_fs_generation_uses_the_trust_context_builder(self):
        """core/fs_template_service.py's context_builders dict is function-local,
        so this asserts the dispatch behaviourally: patch build_trust_context to
        raise, and the raise proves a unit trust was routed to it rather than
        falling back to build_company_context (which would give a unit trust a
        company-shaped equity presentation)."""
        fy = FinancialYear.objects.create(
            entity=self.unit,
            year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        sentinel = RuntimeError("build_trust_context was reached")
        with patch(
            "core.fs_template_service.build_trust_context", side_effect=sentinel,
        ) as patched:
            with self.assertRaises(RuntimeError) as caught:
                generate_financial_statements(fy.pk)
        self.assertIs(caught.exception, sentinel)
        patched.assert_called_once()
        # Guard against the patch target drifting away from the real builder.
        self.assertTrue(callable(build_trust_context))


@override_settings(STORAGES=STORAGES_OVERRIDE)
class UnitTrustEstablishmentDeedTests(TestCase):
    """A unit trust must never be offered a discretionary trust deed.

    Generating the wrong establishment deed has legal consequences, so the
    routing at core/views.py's entity_detail legal-doc prompt gets a real view
    test rather than a dict assertion.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="ross", email="ross@mcands.com.au", password="x" * 14,
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-for-test", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        # Require2FAMiddleware wants BOTH a confirmed TOTP secret on the user
        # and the step performed this session. Repo convention, see
        # core/tests_entity_identity_panel.py.
        session["2fa_verified"] = True
        session.save()
        self.client_obj = Client.objects.create(name="Deed Routing Client")

    def _entity(self, name, entity_type):
        entity = Entity.objects.create(
            entity_name=name, entity_type=entity_type,
            client=self.client_obj, assigned_accountant=self.user,
        )
        # The prompt builds a wizard URL from the latest FY, so give it one —
        # that also exercises reverse() on the doc_type, which would raise
        # NoReverseMatch if the wizard route rejected it.
        FinancialYear.objects.create(
            entity=entity, year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        return entity

    def _prompt_for(self, entity):
        response = self.client.get(
            reverse("core:entity_detail", args=[entity.pk]), secure=True,
        )
        self.assertEqual(response.status_code, 200)
        return response.context["legal_doc_prompt"]

    def test_unit_trust_is_offered_the_fixed_unit_trust_deed(self):
        prompt = self._prompt_for(self._entity("Minli Unit Trust", "trust_unit"))
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt["doc_type"], "unit_trust_deed")
        self.assertNotEqual(prompt["doc_type"], "discretionary_trust_deed")
        self.assertEqual(prompt["doc_type_label"], "Fixed Unit Trust Deed")
        self.assertTrue(prompt["has_fy"])
        self.assertIn("unit_trust_deed", prompt["wizard_url"])

    def test_discretionary_trust_is_still_offered_the_discretionary_deed(self):
        """The primary-risk guard: the four discretionary trusts on the platform
        must be unaffected by the unit-trust routing."""
        prompt = self._prompt_for(self._entity("Vincent Family Trust", "trust"))
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt["doc_type"], "discretionary_trust_deed")
        self.assertEqual(prompt["doc_type_label"], "Discretionary Trust Deed")

    def test_company_is_still_offered_the_company_package(self):
        prompt = self._prompt_for(self._entity("Example Holdings Pty Ltd", "company"))
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt["doc_type"], "company_establishment")

    def test_a_type_with_no_establishment_document_is_not_prompted(self):
        """The dict is the gate, so a type without an entry is silently not
        prompted rather than raising KeyError on the subscript."""
        self.assertIsNone(self._prompt_for(self._entity("Joe Bloggs", "sole_trader")))
