"""
Security tests for MCS Platform core views.

Tests cover:
- IDOR protection (unauthorized users cannot access other users' entities)
- DELETE via GET prevention (destructive actions require POST)
- Permission checks (read-only users cannot modify data)
- Notification scoping (users only see their own notifications)
- Open redirect prevention
- Admin-only access controls on entity assignments
"""
import uuid
from decimal import Decimal
from datetime import date, timedelta
from django.test import TestCase, Client as TestClient, override_settings
from django.urls import reverse
from accounts.models import User
from core.models import (
    Client, Entity, FinancialYear, EntityOfficer, DepreciationAsset,
    StockItem, MeetingNote, ActivityLog, TrialBalanceLine, AccountMapping,
    AdjustingJournal, JournalLine, EntityChartOfAccount, ClientAccountMapping,
    EvaReview, EvaFinding, EvaFindingSuppression, RiskRule, RiskFlag,
)

# Override static files storage for tests (no manifest needed)
STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STORAGES_OVERRIDE)
class SecurityTestBase(TestCase):
    """Base class with shared setup for security tests."""

    @classmethod
    def setUpTestData(cls):
        # All users need 2FA configured to bypass the Require2FAMiddleware
        two_fa_kwargs = {"totp_secret": "TESTSECRET", "totp_confirmed": True}

        # Admin user
        cls.admin = User.objects.create_user(
            username="admin",
            password="testpass123",
            role=User.Role.ADMIN,
            first_name="Admin",
            last_name="User",
            **two_fa_kwargs,
        )
        # Senior accountant
        cls.senior = User.objects.create_user(
            username="senior",
            password="testpass123",
            role=User.Role.SENIOR_ACCOUNTANT,
            first_name="Senior",
            last_name="Accountant",
            **two_fa_kwargs,
        )
        # Regular accountant
        cls.accountant = User.objects.create_user(
            username="accountant",
            password="testpass123",
            role=User.Role.ACCOUNTANT,
            first_name="Regular",
            last_name="Accountant",
            **two_fa_kwargs,
        )
        # Another accountant (for IDOR tests)
        cls.other_accountant = User.objects.create_user(
            username="other_acct",
            password="testpass123",
            role=User.Role.ACCOUNTANT,
            first_name="Other",
            last_name="Accountant",
            **two_fa_kwargs,
        )
        # Read-only user
        cls.readonly = User.objects.create_user(
            username="readonly",
            password="testpass123",
            role=User.Role.READ_ONLY,
            first_name="Read",
            last_name="Only",
            **two_fa_kwargs,
        )

        # Create entities assigned to specific users
        cls.client_obj = Client.objects.create(name="Test Client")
        cls.entity = Entity.objects.create(
            entity_name="Test Entity",
            entity_type="company",
            client=cls.client_obj,
            assigned_accountant=cls.accountant,
        )
        cls.other_entity = Entity.objects.create(
            entity_name="Other Entity",
            entity_type="trust",
            client=cls.client_obj,
            assigned_accountant=cls.other_accountant,
        )

        # Create financial years
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )
        cls.other_fy = FinancialYear.objects.create(
            entity=cls.other_entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )

    def setUp(self):
        self.client = TestClient()

    def login_as(self, user):
        # Skip 2FA check for tests
        self.client.force_login(user)


class IDORProtectionTests(SecurityTestBase):
    """Test that users cannot access entities they are not assigned to."""

    def test_accountant_can_access_own_entity(self):
        self.login_as(self.accountant)
        response = self.client.get(
            reverse("core:entity_detail", args=[self.entity.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_accountant_cannot_access_other_entity(self):
        self.login_as(self.accountant)
        response = self.client.get(
            reverse("core:entity_detail", args=[self.other_entity.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_access_any_entity(self):
        self.login_as(self.admin)
        response = self.client.get(
            reverse("core:entity_detail", args=[self.other_entity.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_accountant_cannot_view_other_officers(self):
        self.login_as(self.accountant)
        response = self.client.get(
            reverse("core:entity_officers", args=[self.other_entity.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_accountant_cannot_create_officer_on_other_entity(self):
        self.login_as(self.accountant)
        response = self.client.post(
            reverse("core:entity_officer_create", args=[self.other_entity.pk]),
            {"full_name": "Hacker Officer", "role": "director"},
        )
        self.assertEqual(response.status_code, 403)

    def test_accountant_cannot_access_other_fy_adjustment_list(self):
        self.login_as(self.accountant)
        response = self.client.get(
            reverse("core:adjustment_list", args=[self.other_fy.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_accountant_cannot_generate_docs_for_other_entity(self):
        self.login_as(self.accountant)
        response = self.client.get(
            reverse("core:generate_document", args=[self.other_fy.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_accountant_cannot_delete_unfinalised_fy_other_entity(self):
        self.login_as(self.accountant)
        response = self.client.post(
            reverse("core:delete_unfinalised_fy", args=[self.other_entity.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_accountant_cannot_add_depreciation_to_other_fy(self):
        self.login_as(self.accountant)
        response = self.client.post(
            reverse("core:depreciation_add", args=[self.other_fy.pk]),
            {"asset_name": "Hacked", "category": "Other",
             "total_cost": "1000", "opening_wdv": "800",
             "method": "D", "rate": "20"},
        )
        self.assertEqual(response.status_code, 403)

    def test_accountant_cannot_add_stock_to_other_fy(self):
        self.login_as(self.accountant)
        response = self.client.post(
            reverse("core:stock_add", args=[self.other_fy.pk]),
            {"item_name": "Hacked Stock", "opening_quantity": "10",
             "opening_value": "100", "closing_quantity": "8",
             "closing_value": "80"},
        )
        self.assertEqual(response.status_code, 403)

    def test_accountant_cannot_create_meeting_note_on_other_entity(self):
        self.login_as(self.accountant)
        response = self.client.post(
            reverse("core:meeting_note_create", args=[self.other_entity.pk]),
            {"title": "Hacked Note", "content": "test",
             "meeting_date": "2025-01-01"},
        )
        self.assertEqual(response.status_code, 403)


class DeleteViaGetTests(SecurityTestBase):
    """Test that destructive operations reject GET requests."""

    def test_officer_delete_rejects_get(self):
        officer = EntityOfficer.objects.create(
            entity=self.entity,
            full_name="Test Officer",
            role="director",
        )
        self.login_as(self.accountant)
        response = self.client.get(
            reverse("core:entity_officer_delete", args=[officer.pk])
        )
        self.assertEqual(response.status_code, 405)
        # Verify officer not deleted
        self.assertTrue(EntityOfficer.objects.filter(pk=officer.pk).exists())

    def test_depreciation_delete_rejects_get(self):
        asset = DepreciationAsset.objects.create(
            financial_year=self.fy,
            asset_name="Test Asset",
            category="Other",
            total_cost=Decimal("1000"),
            opening_wdv=Decimal("800"),
            method="D",
            rate=Decimal("20"),
        )
        self.login_as(self.accountant)
        response = self.client.get(
            reverse("core:depreciation_delete", args=[asset.pk])
        )
        self.assertEqual(response.status_code, 405)
        self.assertTrue(DepreciationAsset.objects.filter(pk=asset.pk).exists())

    def test_stock_delete_rejects_get(self):
        item = StockItem.objects.create(
            financial_year=self.fy,
            item_name="Test Item",
            opening_quantity=Decimal("10"),
            opening_value=Decimal("100"),
            closing_quantity=Decimal("8"),
            closing_value=Decimal("80"),
        )
        self.login_as(self.accountant)
        response = self.client.get(
            reverse("core:stock_delete", args=[item.pk])
        )
        self.assertEqual(response.status_code, 405)
        self.assertTrue(StockItem.objects.filter(pk=item.pk).exists())

    def test_depreciation_roll_forward_rejects_get(self):
        self.login_as(self.accountant)
        response = self.client.get(
            reverse("core:depreciation_roll_forward", args=[self.fy.pk])
        )
        self.assertEqual(response.status_code, 405)

    def test_mark_notification_read_rejects_get(self):
        n = ActivityLog.objects.create(
            user=self.accountant,
            event_type="general",
            title="Test",
            is_read=False,
        )
        self.login_as(self.accountant)
        response = self.client.get(
            reverse("core:mark_notification_read", args=[n.pk])
        )
        self.assertEqual(response.status_code, 405)

    def test_mark_all_notifications_rejects_get(self):
        self.login_as(self.accountant)
        response = self.client.get(
            reverse("core:mark_all_notifications_read")
        )
        self.assertEqual(response.status_code, 405)


class PermissionCheckTests(SecurityTestBase):
    """Test that read-only users cannot perform write operations."""

    def test_readonly_cannot_create_entity(self):
        self.login_as(self.readonly)
        response = self.client.post(
            reverse("core:entity_create"),
            {"entity_name": "Hacked Entity", "entity_type": "company"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Entity.objects.filter(entity_name="Hacked Entity").exists())

    def test_readonly_cannot_create_officer(self):
        self.login_as(self.readonly)
        response = self.client.post(
            reverse("core:entity_officer_create", args=[self.entity.pk]),
            {"full_name": "Hacker", "role": "director"},
        )
        # Should redirect with permission error (or 403 from IDOR)
        self.assertIn(response.status_code, [302, 403])
        self.assertFalse(
            EntityOfficer.objects.filter(full_name="Hacker").exists()
        )

    def test_readonly_cannot_delete_officer(self):
        officer = EntityOfficer.objects.create(
            entity=self.entity,
            full_name="Protected Officer",
            role="director",
        )
        self.login_as(self.readonly)
        response = self.client.post(
            reverse("core:entity_officer_delete", args=[officer.pk])
        )
        # Should get 302 (redirect with error) or 403
        self.assertIn(response.status_code, [302, 403])
        self.assertTrue(EntityOfficer.objects.filter(pk=officer.pk).exists())

    def test_readonly_cannot_add_depreciation(self):
        self.login_as(self.readonly)
        response = self.client.post(
            reverse("core:depreciation_add", args=[self.fy.pk]),
            {"asset_name": "Hacked Asset", "category": "Other",
             "total_cost": "1000", "opening_wdv": "800",
             "method": "D", "rate": "20"},
        )
        # Should get 403 from IDOR or permission check
        self.assertIn(response.status_code, [302, 403])
        self.assertFalse(
            DepreciationAsset.objects.filter(asset_name="Hacked Asset").exists()
        )

    def test_readonly_cannot_add_stock(self):
        self.login_as(self.readonly)
        response = self.client.post(
            reverse("core:stock_add", args=[self.fy.pk]),
            {"item_name": "Hacked Stock", "opening_quantity": "10",
             "opening_value": "100", "closing_quantity": "8",
             "closing_value": "80"},
        )
        self.assertIn(response.status_code, [302, 403])
        self.assertFalse(
            StockItem.objects.filter(item_name="Hacked Stock").exists()
        )

    def test_readonly_cannot_create_meeting_note(self):
        self.login_as(self.readonly)
        response = self.client.post(
            reverse("core:meeting_note_create", args=[self.entity.pk]),
            {"title": "Hacked Note", "content": "test",
             "meeting_date": "2025-01-01"},
        )
        self.assertIn(response.status_code, [302, 403])
        self.assertFalse(
            MeetingNote.objects.filter(title="Hacked Note").exists()
        )


class NotificationScopingTests(SecurityTestBase):
    """Test that notification endpoints are scoped to the requesting user."""

    def test_mark_all_read_only_affects_own(self):
        # Create notifications for two different users
        n1 = ActivityLog.objects.create(
            user=self.accountant,
            event_type="general",
            title="Accountant's notification",
            is_read=False,
        )
        n2 = ActivityLog.objects.create(
            user=self.other_accountant,
            event_type="general",
            title="Other's notification",
            is_read=False,
        )

        self.login_as(self.accountant)
        response = self.client.post(reverse("core:mark_all_notifications_read"))
        self.assertEqual(response.status_code, 200)

        n1.refresh_from_db()
        n2.refresh_from_db()
        self.assertTrue(n1.is_read)
        self.assertFalse(n2.is_read)  # Should NOT be marked read

    def test_notifications_api_only_returns_own(self):
        ActivityLog.objects.create(
            user=self.accountant,
            event_type="general",
            title="My notification",
            is_read=False,
        )
        ActivityLog.objects.create(
            user=self.other_accountant,
            event_type="general",
            title="Not mine",
            is_read=False,
        )

        self.login_as(self.accountant)
        response = self.client.get(reverse("core:notifications_api"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["unread_count"], 1)
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["title"], "My notification")

    def test_cannot_mark_other_user_notification_read(self):
        n = ActivityLog.objects.create(
            user=self.other_accountant,
            event_type="general",
            title="Other's notification",
            is_read=False,
        )
        self.login_as(self.accountant)
        response = self.client.post(
            reverse("core:mark_notification_read", args=[n.pk])
        )
        self.assertEqual(response.status_code, 404)  # Should not find it
        n.refresh_from_db()
        self.assertFalse(n.is_read)


class EntityAssignmentPermissionTests(SecurityTestBase):
    """Test that entity assignment views require senior/admin access."""

    def test_accountant_cannot_view_assignments(self):
        self.login_as(self.accountant)
        response = self.client.get(reverse("core:entity_assignments"))
        self.assertEqual(response.status_code, 302)  # Redirected

    def test_readonly_cannot_view_assignments(self):
        self.login_as(self.readonly)
        response = self.client.get(reverse("core:entity_assignments"))
        self.assertEqual(response.status_code, 302)

    def test_senior_can_view_assignments(self):
        self.login_as(self.senior)
        response = self.client.get(reverse("core:entity_assignments"))
        self.assertEqual(response.status_code, 200)

    def test_admin_can_view_assignments(self):
        self.login_as(self.admin)
        response = self.client.get(reverse("core:entity_assignments"))
        self.assertEqual(response.status_code, 200)

    def test_accountant_cannot_bulk_assign(self):
        self.login_as(self.accountant)
        response = self.client.post(
            reverse("core:bulk_assign_entities"),
            {"entity_ids": [str(self.entity.pk)],
             "primary_accountant_id": str(self.accountant.pk)},
        )
        self.assertEqual(response.status_code, 302)


class EntityFormSecurityTests(SecurityTestBase):
    """Test that the EntityForm restricts fields based on user role."""

    def test_non_senior_cannot_set_assigned_accountant(self):
        """Non-senior users should not see assigned_accountant field."""
        from core.forms import EntityForm
        form = EntityForm(user=self.accountant)
        self.assertNotIn("assigned_accountant", form.fields)

    def test_senior_can_set_assigned_accountant(self):
        """Senior users should see assigned_accountant field."""
        from core.forms import EntityForm
        form = EntityForm(user=self.senior)
        self.assertIn("assigned_accountant", form.fields)

    def test_admin_can_set_assigned_accountant(self):
        """Admin users should see assigned_accountant field."""
        from core.forms import EntityForm
        form = EntityForm(user=self.admin)
        self.assertIn("assigned_accountant", form.fields)


class MassAssignmentProtectionTests(SecurityTestBase):
    """Test that Decimal parsing errors don't cause 500 errors."""

    def test_invalid_decimal_depreciation_add(self):
        self.login_as(self.accountant)
        response = self.client.post(
            reverse("core:depreciation_add", args=[self.fy.pk]),
            {"asset_name": "Test", "category": "Other",
             "total_cost": "not_a_number", "opening_wdv": "800",
             "method": "D", "rate": "20"},
        )
        # Should redirect with error, not 500
        self.assertIn(response.status_code, [302, 200])
        self.assertFalse(
            DepreciationAsset.objects.filter(asset_name="Test").exists()
        )

    def test_invalid_decimal_stock_add(self):
        self.login_as(self.accountant)
        response = self.client.post(
            reverse("core:stock_add", args=[self.fy.pk]),
            {"item_name": "Test Stock", "opening_quantity": "invalid",
             "opening_value": "100", "closing_quantity": "8",
             "closing_value": "80"},
        )
        self.assertIn(response.status_code, [302, 200])
        self.assertFalse(
            StockItem.objects.filter(item_name="Test Stock").exists()
        )


# ---------------------------------------------------------------------------
# Auto Tax Provision Tests
# ---------------------------------------------------------------------------
@override_settings(STORAGES=STORAGES_OVERRIDE)
class TaxProvisionTestCase(TestCase):
    """Tests for the auto tax provision status and post views."""

    @classmethod
    def setUpTestData(cls):
        two_fa_kwargs = {"totp_secret": "TESTSECRET", "totp_confirmed": True}
        cls.admin = User.objects.create_user(
            username="tp_admin", password="testpass123",
            role=User.Role.ADMIN, first_name="Admin", last_name="User",
            **two_fa_kwargs,
        )
        cls.accountant = User.objects.create_user(
            username="tp_accountant", password="testpass123",
            role=User.Role.ACCOUNTANT, first_name="Test", last_name="Acct",
            **two_fa_kwargs,
        )
        cls.readonly = User.objects.create_user(
            username="tp_readonly", password="testpass123",
            role=User.Role.READ_ONLY, first_name="Read", last_name="Only",
            **two_fa_kwargs,
        )
        cls.client_obj = Client.objects.create(name="TP Test Client")

        # Company entity with base rate set
        cls.entity = Entity.objects.create(
            entity_name="TP Company",
            entity_type="company",
            client=cls.client_obj,
            assigned_accountant=cls.accountant,
            is_base_rate_entity=True,
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
            status="in_review",
        )

        # Trust entity (not eligible)
        cls.trust_entity = Entity.objects.create(
            entity_name="TP Trust",
            entity_type="trust",
            client=cls.client_obj,
            assigned_accountant=cls.accountant,
        )
        cls.trust_fy = FinancialYear.objects.create(
            entity=cls.trust_entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )

        # Create AccountMapping for P&L section (Revenue)
        cls.revenue_mapping = AccountMapping.objects.create(
            standard_code="REV001",
            line_item_label="Sales Revenue",
            financial_statement="income_statement",
            statement_section="Revenue",
            display_order=100,
        )
        cls.expense_mapping = AccountMapping.objects.create(
            standard_code="EXP001",
            line_item_label="Operating Expenses",
            financial_statement="income_statement",
            statement_section="Expenses",
            display_order=200,
        )

    def setUp(self):
        self.client = TestClient()

    def login_as(self, user):
        self.client.force_login(user)

    def _create_tb_lines(self, fy, revenue=Decimal("100000"), expenses=Decimal("60000")):
        """Create trial balance lines producing net_profit = revenue - expenses."""
        TrialBalanceLine.objects.filter(financial_year=fy).delete()
        TrialBalanceLine.objects.create(
            financial_year=fy,
            account_code="1000",
            account_name="Sales Revenue",
            debit=Decimal("0"),
            credit=revenue,
            mapped_line_item=self.revenue_mapping,
        )
        TrialBalanceLine.objects.create(
            financial_year=fy,
            account_code="5000",
            account_name="Operating Expenses",
            debit=expenses,
            credit=Decimal("0"),
            mapped_line_item=self.expense_mapping,
        )

    # --- Status endpoint tests ---

    def test_status_not_company(self):
        """Trust entity should not be eligible."""
        self.login_as(self.accountant)
        url = reverse("core:tax_provision_status", args=[self.trust_fy.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["eligible"])
        self.assertIn("company", data["reason"])

    def test_status_base_rate_not_set(self):
        """Entity with is_base_rate_entity=None should be ineligible."""
        self.login_as(self.admin)
        entity = Entity.objects.create(
            entity_name="No BRE",
            entity_type="company",
            client=self.client_obj,
            is_base_rate_entity=None,
        )
        fy = FinancialYear.objects.create(
            entity=entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )
        url = reverse("core:tax_provision_status", args=[fy.pk])
        response = self.client.get(url)
        data = response.json()
        self.assertFalse(data["eligible"])
        self.assertIn("Base Rate Entity", data["reason"])

    def test_status_loss_position(self):
        """Entity in a loss position should show not eligible."""
        self.login_as(self.accountant)
        # expenses > revenue => loss
        self._create_tb_lines(self.fy, revenue=Decimal("50000"), expenses=Decimal("80000"))
        url = reverse("core:tax_provision_status", args=[self.fy.pk])
        response = self.client.get(url)
        data = response.json()
        self.assertFalse(data["eligible"])
        self.assertIn("loss", data["reason"])

    def test_status_eligible_base_rate(self):
        """Eligible company with base rate should return correct calculations."""
        self.login_as(self.accountant)
        self._create_tb_lines(self.fy, revenue=Decimal("100000"), expenses=Decimal("60000"))
        url = reverse("core:tax_provision_status", args=[self.fy.pk])
        response = self.client.get(url)
        data = response.json()
        self.assertTrue(data["eligible"])
        self.assertEqual(Decimal(data["net_profit"]), Decimal("40000"))
        self.assertEqual(data["tax_rate"], "0.25")
        self.assertEqual(data["rate_label"], "25% (Base Rate Entity)")
        # 40000 * 0.25 = 10000
        self.assertEqual(Decimal(data["calculated_tax"]), Decimal("10000"))
        self.assertEqual(Decimal(data["existing_provision"]), Decimal("0"))
        self.assertEqual(Decimal(data["adjustment_required"]), Decimal("10000"))

    def test_status_standard_rate(self):
        """Non-base-rate entity should use 30%."""
        self.login_as(self.admin)
        entity = Entity.objects.create(
            entity_name="Std Rate Co",
            entity_type="company",
            client=self.client_obj,
            is_base_rate_entity=False,
        )
        fy = FinancialYear.objects.create(
            entity=entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )
        self._create_tb_lines(fy, revenue=Decimal("100000"), expenses=Decimal("60000"))
        url = reverse("core:tax_provision_status", args=[fy.pk])
        response = self.client.get(url)
        data = response.json()
        self.assertTrue(data["eligible"])
        self.assertEqual(data["tax_rate"], "0.30")
        # 40000 * 0.30 = 12000
        self.assertEqual(Decimal(data["calculated_tax"]), Decimal("12000"))

    def test_status_existing_provision_journal(self):
        """If a tax_provision journal already exists, should be ineligible."""
        self.login_as(self.accountant)
        self._create_tb_lines(self.fy)
        AdjustingJournal.objects.create(
            financial_year=self.fy,
            journal_type="tax_provision",
            journal_date=self.fy.end_date,
            description="Tax provision for year ended 30 June 2025",
            created_by=self.accountant,
            status="posted",
            total_debit=Decimal("10000"),
            total_credit=Decimal("10000"),
        )
        url = reverse("core:tax_provision_status", args=[self.fy.pk])
        response = self.client.get(url)
        data = response.json()
        self.assertFalse(data["eligible"])
        self.assertIn("already exists", data["reason"])

    def test_status_with_existing_tb_provision(self):
        """Existing provision balance in TB should reduce adjustment."""
        self.login_as(self.accountant)
        self._create_tb_lines(self.fy, revenue=Decimal("100000"), expenses=Decimal("60000"))
        # Add existing provision (credit balance on 3325)
        TrialBalanceLine.objects.create(
            financial_year=self.fy,
            account_code="3325",
            account_name="Taxation",
            debit=Decimal("0"),
            credit=Decimal("3000"),
        )
        url = reverse("core:tax_provision_status", args=[self.fy.pk])
        response = self.client.get(url)
        data = response.json()
        self.assertTrue(data["eligible"])
        self.assertEqual(Decimal(data["existing_provision"]), Decimal("3000"))
        # 10000 - 3000 = 7000
        self.assertEqual(Decimal(data["adjustment_required"]), Decimal("7000"))

    # --- Post endpoint tests ---

    def test_post_creates_journal(self):
        """POST should create a tax_provision journal and TB lines."""
        self.login_as(self.accountant)
        self._create_tb_lines(self.fy, revenue=Decimal("100000"), expenses=Decimal("60000"))
        url = reverse("core:auto_tax_provision", args=[self.fy.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["adjustment_amount"], "10000")

        # Verify journal was created
        journal = AdjustingJournal.objects.get(
            financial_year=self.fy, journal_type="tax_provision",
        )
        self.assertEqual(journal.total_debit, Decimal("10000"))
        self.assertEqual(journal.total_credit, Decimal("10000"))
        self.assertEqual(journal.status, "posted")

        # Verify journal lines
        lines = journal.lines.order_by("line_number")
        self.assertEqual(lines.count(), 2)
        self.assertEqual(lines[0].account_code, "4110")
        self.assertEqual(lines[0].debit, Decimal("10000"))
        self.assertEqual(lines[1].account_code, "3325")
        self.assertEqual(lines[1].credit, Decimal("10000"))

        # Verify TB lines were created
        tb_adjustments = TrialBalanceLine.objects.filter(
            financial_year=self.fy, is_adjustment=True,
            source="manual_journal",
        )
        self.assertTrue(tb_adjustments.exists())

    def test_post_not_company(self):
        """POST for trust entity should fail."""
        self.login_as(self.accountant)
        url = reverse("core:auto_tax_provision", args=[self.trust_fy.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 400)

    def test_post_get_not_allowed(self):
        """GET should not be allowed on the post endpoint."""
        self.login_as(self.accountant)
        url = reverse("core:auto_tax_provision", args=[self.fy.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 405)

    def test_post_duplicate_prevented(self):
        """Second POST should fail if journal already exists."""
        self.login_as(self.accountant)
        self._create_tb_lines(self.fy, revenue=Decimal("100000"), expenses=Decimal("60000"))
        url = reverse("core:auto_tax_provision", args=[self.fy.pk])
        response1 = self.client.post(url)
        self.assertEqual(response1.status_code, 200)
        response2 = self.client.post(url)
        self.assertEqual(response2.status_code, 400)
        data = response2.json()
        self.assertIn("already exists", data["error"])

    def test_post_with_existing_provision_adjusts(self):
        """POST with existing TB provision should only post the difference."""
        self.login_as(self.accountant)
        self._create_tb_lines(self.fy, revenue=Decimal("100000"), expenses=Decimal("60000"))
        TrialBalanceLine.objects.create(
            financial_year=self.fy,
            account_code="3325",
            account_name="Taxation",
            debit=Decimal("0"),
            credit=Decimal("3000"),
        )
        url = reverse("core:auto_tax_provision", args=[self.fy.pk])
        response = self.client.post(url)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["adjustment_amount"], "7000")
        journal = AdjustingJournal.objects.get(
            financial_year=self.fy, journal_type="tax_provision",
        )
        self.assertEqual(journal.total_debit, Decimal("7000"))

    def test_post_loss_position_rejected(self):
        """POST with loss position should fail."""
        self.login_as(self.accountant)
        self._create_tb_lines(self.fy, revenue=Decimal("30000"), expenses=Decimal("80000"))
        url = reverse("core:auto_tax_provision", args=[self.fy.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 400)

    def test_post_rounding_up(self):
        """Tax amount should be rounded up (ceiling)."""
        self.login_as(self.accountant)
        # net_profit = 100001 - 60000 = 40001, tax = 40001 * 0.25 = 10000.25, ceil = 10001
        self._create_tb_lines(self.fy, revenue=Decimal("100001"), expenses=Decimal("60000"))
        url = reverse("core:auto_tax_provision", args=[self.fy.pk])
        response = self.client.post(url)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["adjustment_amount"], "10001")

    def test_post_no_adjustment_needed(self):
        """POST should fail if existing provision covers calculated tax."""
        self.login_as(self.accountant)
        self._create_tb_lines(self.fy, revenue=Decimal("100000"), expenses=Decimal("60000"))
        # Existing provision = 10000 which equals calculated tax
        TrialBalanceLine.objects.create(
            financial_year=self.fy,
            account_code="3325",
            account_name="Taxation",
            debit=Decimal("0"),
            credit=Decimal("10000"),
        )
        url = reverse("core:auto_tax_provision", args=[self.fy.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("No adjustment required", data["error"])

    def test_custom_account_mapping(self):
        """If ClientAccountMapping points to IS-TAX-001, use that account code."""
        self.login_as(self.accountant)
        self._create_tb_lines(self.fy, revenue=Decimal("100000"), expenses=Decimal("60000"))

        tax_expense_map = AccountMapping.objects.create(
            standard_code="IS-TAX-001",
            line_item_label="Income tax expense",
            financial_statement="income_statement",
            statement_section="Income Tax",
            display_order=900,
        )
        ClientAccountMapping.objects.create(
            entity=self.entity,
            client_account_code="9100",
            client_account_name="Custom Tax Expense",
            mapped_line_item=tax_expense_map,
        )
        url = reverse("core:auto_tax_provision", args=[self.fy.pk])
        response = self.client.post(url)
        data = response.json()
        self.assertTrue(data["success"])

        journal = AdjustingJournal.objects.get(
            financial_year=self.fy, journal_type="tax_provision",
        )
        expense_line = journal.lines.get(line_number=1)
        self.assertEqual(expense_line.account_code, "9100")
        self.assertEqual(expense_line.account_name, "Custom Tax Expense")


# ===========================================================================
# Division 7A False Positive Tests
# ===========================================================================

@override_settings(STORAGES=STORAGES_OVERRIDE)
class Div7AFalsePositiveTestCase(TestCase):
    """
    Verify that the Div 7A assessment engine does not raise false positives
    for loan accounts with zero or credit closing balances.

    The ``models_Q_director_or_shareholder`` helper uses a JSONField
    ``__contains`` lookup which is unsupported on SQLite, so we patch it
    to return an empty queryset for these tests (s 109E officer detection
    is not the focus here).
    """

    @classmethod
    def setUpTestData(cls):
        two_fa_kwargs = {"totp_secret": "TESTSECRET", "totp_confirmed": True}
        cls.admin = User.objects.create_user(
            username="div7a_admin",
            password="testpass123",
            role=User.Role.ADMIN,
            first_name="Admin",
            last_name="User",
            **two_fa_kwargs,
        )
        cls.client_obj = Client.objects.create(name="Div7A Test Client")

    def _make_entity_and_fy(self, entity_name="Test Pty Ltd"):
        """Helper: create a fresh company Entity + FY for each test."""
        entity = Entity.objects.create(
            entity_name=entity_name,
            entity_type="company",
            client=self.client_obj,
            assigned_accountant=self.admin,
        )
        fy = FinancialYear.objects.create(
            entity=entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )
        return entity, fy

    def _create_tb_line(self, fy, account_code, account_name,
                        debit=0, credit=0, prior_debit=0, prior_credit=0):
        """Helper: create a TrialBalanceLine."""
        return TrialBalanceLine.objects.create(
            financial_year=fy,
            account_code=account_code,
            account_name=account_name,
            debit=Decimal(str(debit)),
            credit=Decimal(str(credit)),
            prior_debit=Decimal(str(prior_debit)),
            prior_credit=Decimal(str(prior_credit)),
        )

    @staticmethod
    def _sqlite_safe_q():
        """Return a Q that works on SQLite (no JSONField __contains)."""
        from django.db.models import Q
        return Q(role__in=["director", "shareholder"])

    def test_div7a_fires_for_genuine_debit_balance(self):
        """A loan account with a genuine debit balance should trigger Div 7A."""
        from unittest.mock import patch
        from core.eva_div7a import run_div7a_assessment

        entity, fy = self._make_entity_and_fy("Debit Balance Co")
        # Director loan with $303,165.70 debit (money owed by director)
        self._create_tb_line(
            fy, "3100", "Loan - Director Smith",
            debit=303165.70, credit=0,
            prior_debit=200000, prior_credit=0,
        )

        with patch("core.eva_div7a.models_Q_director_or_shareholder", self._sqlite_safe_q):
            result = run_div7a_assessment(str(fy.pk))
        self.assertNotIn("skipped", result)
        self.assertIn("T2-D7A-01", result.get("rules_fired", []))
        self.assertEqual(result["overall_severity"], "CRITICAL")
        self.assertGreater(Decimal(result["total_exposure"]), Decimal("0"))

    def test_div7a_does_not_fire_for_zero_balance(self):
        """A loan account with zero balance (fully repaid) should NOT trigger."""
        from unittest.mock import patch
        from core.eva_div7a import run_div7a_assessment

        entity, fy = self._make_entity_and_fy("Zero Balance Co")
        # Director loan fully repaid — debit and credit are both zero
        self._create_tb_line(
            fy, "3100", "Loan - Director Smith",
            debit=0, credit=0,
            prior_debit=50000, prior_credit=0,
        )

        with patch("core.eva_div7a.models_Q_director_or_shareholder", self._sqlite_safe_q):
            result = run_div7a_assessment(str(fy.pk))
        self.assertNotIn("T2-D7A-01", result.get("rules_fired", []))
        self.assertEqual(result["overall_severity"], "CLEAR")
        self.assertEqual(Decimal(result["total_exposure"]), Decimal("0"))

    def test_div7a_does_not_fire_for_credit_balance(self):
        """A loan account with credit balance (company owes person) should NOT trigger."""
        from unittest.mock import patch
        from core.eva_div7a import run_div7a_assessment

        entity, fy = self._make_entity_and_fy("Credit Balance Co")
        # Prior year had a credit balance, still credit
        self._create_tb_line(
            fy, "3100", "Loan - Director Smith",
            debit=0, credit=52680.27,
            prior_debit=0, prior_credit=30000,
        )

        with patch("core.eva_div7a.models_Q_director_or_shareholder", self._sqlite_safe_q):
            result = run_div7a_assessment(str(fy.pk))
        self.assertNotIn("T2-D7A-01", result.get("rules_fired", []))
        self.assertEqual(result["overall_severity"], "CLEAR")
        self.assertEqual(Decimal(result["total_exposure"]), Decimal("0"))

    def test_div7a_consolidated_excludes_zero_and_credit_accounts(self):
        """Only accounts with positive net balance should contribute to exposure."""
        from unittest.mock import patch
        from core.eva_div7a import run_div7a_assessment

        entity, fy = self._make_entity_and_fy("Mixed Balances Co")
        # Account 1: genuine debit — should fire
        self._create_tb_line(
            fy, "3100", "Loan - Director Alpha",
            debit=100000, credit=0,
            prior_debit=50000, prior_credit=0,
        )
        # Account 2: zero balance — should NOT contribute
        self._create_tb_line(
            fy, "3200", "Loan - Director Beta",
            debit=0, credit=0,
            prior_debit=80000, prior_credit=0,
        )
        # Account 3: credit balance — should NOT contribute
        self._create_tb_line(
            fy, "3300", "Loan - Director Gamma",
            debit=0, credit=25000,
            prior_debit=0, prior_credit=10000,
        )

        with patch("core.eva_div7a.models_Q_director_or_shareholder", self._sqlite_safe_q):
            result = run_div7a_assessment(str(fy.pk))
        # Only Account 1 should fire
        self.assertIn("T2-D7A-01", result.get("rules_fired", []))
        # The total exposure should only include Account 1's movement
        # CY balance 100000 - PY balance 50000 = 50000 movement
        total = Decimal(result["total_exposure"])
        # It should be exactly 50000 from the loan (plus any s109e/upe which
        # are zero here), NOT inflated by zero/credit accounts.
        self.assertEqual(total, Decimal("50000.00"))

    def test_div7a_consolidated_not_generated_when_total_is_zero(self):
        """No Eva findings should be created when all loan accounts are zero/credit."""
        from unittest.mock import patch
        from core.eva_div7a import run_div7a_assessment
        from core.models import EvaFinding

        entity, fy = self._make_entity_and_fy("All Clear Co")
        # Zero balance loan
        self._create_tb_line(
            fy, "3100", "Loan - Director Smith",
            debit=0, credit=0,
            prior_debit=100000, prior_credit=0,
        )
        # Credit balance loan
        self._create_tb_line(
            fy, "3200", "Shareholder Loan - Jones",
            debit=0, credit=30000,
            prior_debit=0, prior_credit=20000,
        )

        with patch("core.eva_div7a.models_Q_director_or_shareholder", self._sqlite_safe_q):
            result = run_div7a_assessment(str(fy.pk))
        self.assertEqual(result["overall_severity"], "CLEAR")
        self.assertEqual(result["rules_fired"], [])

        # No EvaFindings should exist for this FY
        findings = EvaFinding.objects.filter(
            eva_review__financial_year=fy,
            check_name="div7a",
        )
        self.assertEqual(findings.count(), 0)


@override_settings(STORAGES=STORAGES_OVERRIDE)
class GSTMovementTestCase(TestCase):
    """Test that Eva's GST reconciliation check uses net effective balances,
    not raw TB rows, to prevent movement figure doubling."""

    @classmethod
    def setUpTestData(cls):
        two_fa_kwargs = {"totp_secret": "TESTSECRET", "totp_confirmed": True}
        cls.admin = User.objects.create_user(
            username="gst_admin",
            password="testpass123",
            role=User.Role.ADMIN,
            first_name="GST",
            last_name="Admin",
            **two_fa_kwargs,
        )
        cls.client_obj = Client.objects.create(name="GST Test Client")
        cls.entity = Entity.objects.create(
            entity_name="GST Test Entity",
            entity_type="company",
            client=cls.client_obj,
            assigned_accountant=cls.admin,
            is_gst_registered=True,
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )

    def test_gst_movement_uses_net_not_gross(self):
        """Movement figures must be computed from effective (aggregated) balances,
        not from raw individual TB rows. Reproduces the doubling bug with
        live data for accounts 3380 and 3389."""
        from core.eva_engine import _build_check_context

        # Account 3380 GST payable: CY dr=5140.64, cr=0, PY dr=0, cr=17928.96
        TrialBalanceLine.objects.create(
            financial_year=self.fy,
            account_code="3380",
            account_name="GST Payable",
            debit=Decimal("5140.64"),
            credit=Decimal("0"),
            prior_debit=Decimal("0"),
            prior_credit=Decimal("17928.96"),
        )

        # Account 3389 GST clearing: CY dr=0, cr=0, PY dr=0, cr=7670.73
        TrialBalanceLine.objects.create(
            financial_year=self.fy,
            account_code="3389",
            account_name="GST Clearing",
            debit=Decimal("0"),
            credit=Decimal("0"),
            prior_debit=Decimal("0"),
            prior_credit=Decimal("7670.73"),
        )

        context = _build_check_context(self.fy, "gst_reconciliation")

        # Correct movement for 3380: cy_net=5140.64, py_net=-17928.96, movement=23069.60
        self.assertIn("23,069.60", context)
        # The doubled (incorrect) value must NOT appear
        self.assertNotIn("40,998.56", context)

        # Correct movement for 3389: cy_net=0, py_net=-7670.73, movement=7670.73
        self.assertIn("7,670.73", context)
        # The doubled (incorrect) value must NOT appear
        self.assertNotIn("15,341.46", context)

        # Must contain the anti-doubling instruction
        self.assertIn("Do NOT sum raw debit/credit columns", context)

    def test_gst_movement_pct_calculated_from_net_py_balance(self):
        """Movement percentage must be based on net PY balance, not gross."""
        from core.eva_engine import _build_check_context

        # Account with known PY net: PY dr=0, cr=10000 => PY net = -10000
        # CY dr=5000, cr=0 => CY net = 5000
        # Movement = 5000 - (-10000) = 15000
        # Pct = 15000 / abs(-10000) * 100 = 150.0%
        TrialBalanceLine.objects.create(
            financial_year=self.fy,
            account_code="3390",
            account_name="GST Test Account",
            debit=Decimal("5000"),
            credit=Decimal("0"),
            prior_debit=Decimal("0"),
            prior_credit=Decimal("10000"),
        )

        context = _build_check_context(self.fy, "gst_reconciliation")

        # Check the movement figure
        self.assertIn("15,000.00", context)
        # Check the percentage
        self.assertIn("+150.0%", context)


# ===========================================================================
# Eva Finding Persistence — Task 4 tests
# ===========================================================================

@override_settings(STORAGES=STORAGES_OVERRIDE)
class FindingKeyTests(TestCase):
    """Test EvaFinding.build_finding_key deterministic key generation."""

    def test_key_with_no_accounts_or_qualifier(self):
        key = EvaFinding.build_finding_key("gst_reconciliation")
        self.assertEqual(key, "gst_reconciliation")

    def test_key_with_single_account(self):
        key = EvaFinding.build_finding_key("div7a", account_codes=["1200"])
        self.assertEqual(key, "div7a_1200")

    def test_key_with_multiple_accounts_sorted(self):
        key = EvaFinding.build_finding_key("sgc", account_codes=["5000", "2100"])
        self.assertEqual(key, "sgc_2100_5000")

    def test_key_with_qualifier(self):
        key = EvaFinding.build_finding_key("div7a", qualifier="OTHER_EXPOSURES")
        self.assertEqual(key, "div7a_OTHER_EXPOSURES")

    def test_qualifier_takes_precedence_over_accounts(self):
        key = EvaFinding.build_finding_key(
            "div7a", account_codes=["1200"], qualifier="OTHER_EXPOSURES",
        )
        self.assertEqual(key, "div7a_OTHER_EXPOSURES")

    def test_key_is_deterministic(self):
        """Same inputs must always produce the same key."""
        k1 = EvaFinding.build_finding_key("div7a", account_codes=["3000", "1200"])
        k2 = EvaFinding.build_finding_key("div7a", account_codes=["1200", "3000"])
        self.assertEqual(k1, k2)


@override_settings(STORAGES=STORAGES_OVERRIDE)
class FindingAddressedSkipTests(TestCase):
    """Test that addressed findings are not re-created on re-review."""

    @classmethod
    def setUpTestData(cls):
        two_fa_kwargs = {"totp_secret": "TESTSECRET", "totp_confirmed": True}
        cls.user = User.objects.create_user(
            username="eva_tester",
            password="testpass123",
            role=User.Role.ADMIN,
            **two_fa_kwargs,
        )
        cls.client_obj = Client.objects.create(name="Finding Test Client")
        cls.entity = Entity.objects.create(
            entity_name="Finding Test Co",
            entity_type="company",
            client=cls.client_obj,
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )

    def test_is_finding_addressed_returns_false_when_no_prior(self):
        from core.eva_engine import _is_finding_addressed
        self.assertFalse(
            _is_finding_addressed(self.fy, "div7a_1200")
        )

    def test_is_finding_addressed_returns_true_for_addressed_status(self):
        from core.eva_engine import _is_finding_addressed
        review = EvaReview.objects.create(
            financial_year=self.fy, status="findings_raised",
        )
        EvaFinding.objects.create(
            eva_review=review,
            check_name="div7a",
            finding_key="div7a_1200",
            severity="critical",
            plain_english_explanation="test",
            recommendation="test",
            status="addressed",
        )
        self.assertTrue(
            _is_finding_addressed(self.fy, "div7a_1200")
        )

    def test_is_finding_addressed_returns_true_for_closed_status(self):
        from core.eva_engine import _is_finding_addressed
        review = EvaReview.objects.create(
            financial_year=self.fy, status="findings_raised",
        )
        EvaFinding.objects.create(
            eva_review=review,
            check_name="div7a",
            finding_key="div7a_1200",
            severity="critical",
            plain_english_explanation="test",
            recommendation="test",
            status="closed",
        )
        self.assertTrue(
            _is_finding_addressed(self.fy, "div7a_1200")
        )

    def test_is_finding_addressed_returns_false_for_open_status(self):
        from core.eva_engine import _is_finding_addressed
        review = EvaReview.objects.create(
            financial_year=self.fy, status="findings_raised",
        )
        EvaFinding.objects.create(
            eva_review=review,
            check_name="div7a",
            finding_key="div7a_1200",
            severity="critical",
            plain_english_explanation="test",
            recommendation="test",
            status="open",
        )
        self.assertFalse(
            _is_finding_addressed(self.fy, "div7a_1200")
        )

    def test_is_finding_addressed_empty_key_returns_false(self):
        from core.eva_engine import _is_finding_addressed
        self.assertFalse(
            _is_finding_addressed(self.fy, "")
        )

    def test_addressed_finding_across_reviews(self):
        """Addressed finding from review 1 must block creation in review 2."""
        from core.eva_engine import _is_finding_addressed
        review1 = EvaReview.objects.create(
            financial_year=self.fy, status="findings_raised",
        )
        EvaFinding.objects.create(
            eva_review=review1,
            check_name="gst_reconciliation",
            finding_key="gst_reconciliation",
            severity="advisory",
            plain_english_explanation="test",
            recommendation="test",
            status="addressed",
        )
        # A second review for the same FY should see the addressed finding
        _review2 = EvaReview.objects.create(
            financial_year=self.fy, status="pending",
        )
        self.assertTrue(
            _is_finding_addressed(self.fy, "gst_reconciliation")
        )

    def test_finding_key_stored_on_creation(self):
        """finding_key must be persisted when set during creation."""
        review = EvaReview.objects.create(
            financial_year=self.fy, status="findings_raised",
        )
        finding = EvaFinding.objects.create(
            eva_review=review,
            check_name="sgc",
            finding_key="sgc_5000",
            severity="advisory",
            plain_english_explanation="test",
            recommendation="test",
        )
        finding.refresh_from_db()
        self.assertEqual(finding.finding_key, "sgc_5000")

    def test_different_fy_not_affected(self):
        """Addressed finding on one FY must not block another FY."""
        from core.eva_engine import _is_finding_addressed
        review = EvaReview.objects.create(
            financial_year=self.fy, status="findings_raised",
        )
        EvaFinding.objects.create(
            eva_review=review,
            check_name="div7a",
            finding_key="div7a_1200",
            severity="critical",
            plain_english_explanation="test",
            recommendation="test",
            status="addressed",
        )
        # Create a different FY for the same entity
        other_fy = FinancialYear.objects.create(
            entity=self.entity,
            year_label="FY2026",
            start_date=date(2025, 7, 1),
            end_date=date(2026, 6, 30),
        )
        self.assertFalse(
            _is_finding_addressed(other_fy, "div7a_1200")
        )


@override_settings(STORAGES=STORAGES_OVERRIDE)
class FindingKeyDataMigrationTests(TestCase):
    """Test that build_finding_key produces expected keys for Div7A scenarios."""

    def test_div7a_per_account_key(self):
        key = EvaFinding.build_finding_key("div7a", account_codes=["1200"])
        self.assertEqual(key, "div7a_1200")

    def test_div7a_other_exposures_key(self):
        key = EvaFinding.build_finding_key("div7a", qualifier="OTHER_EXPOSURES")
        self.assertEqual(key, "div7a_OTHER_EXPOSURES")


# ============================================================================
# TAX PROVISION MISSING RISK RULE TESTS
# ============================================================================

@override_settings(STORAGES=STORAGES_OVERRIDE)
class TaxProvisionMissingRuleTests(TestCase):
    """Tests for the TAX_PROVISION_MISSING Tier 2 risk rule."""

    @classmethod
    def setUpTestData(cls):
        two_fa_kwargs = {"totp_secret": "TESTSECRET", "totp_confirmed": True}
        cls.admin = User.objects.create_user(
            username="tpm_admin", password="testpass123",
            role=User.Role.ADMIN, first_name="Admin", last_name="User",
            **two_fa_kwargs,
        )
        cls.client_obj = Client.objects.create(name="TPM Test Client")
        cls.entity = Entity.objects.create(
            entity_name="TPM Company",
            entity_type="company",
            client=cls.client_obj,
            is_base_rate_entity=True,
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
            status="in_review",
        )
        cls.revenue_mapping = AccountMapping.objects.create(
            standard_code="TPM-REV",
            line_item_label="Sales Revenue",
            financial_statement="income_statement",
            statement_section="Revenue",
            display_order=100,
        )
        cls.expense_mapping = AccountMapping.objects.create(
            standard_code="TPM-EXP",
            line_item_label="Operating Expenses",
            financial_statement="income_statement",
            statement_section="Expenses",
            display_order=200,
        )
        cls.rule = RiskRule.objects.create(
            rule_id="TAX_PROVISION_MISSING",
            category="general",
            title="Tax Provision Missing",
            description="{entity_name} has net profit of {net_profit} but no income tax provision has been posted.",
            severity="MEDIUM",
            tier=2,
            applicable_entities=["company"],
            trigger_config={"type": "tax_provision"},
            recommended_action="Post an income tax provision journal.",
            legislation_ref="ITAA 1997",
        )

    def _create_profitable_tb(self):
        """Create TB lines with 100k revenue, 60k expenses (40k profit)."""
        TrialBalanceLine.objects.filter(financial_year=self.fy).delete()
        TrialBalanceLine.objects.create(
            financial_year=self.fy,
            account_code="4000",
            account_name="Sales Revenue",
            debit=Decimal("0"),
            credit=Decimal("100000"),
            mapped_line_item=self.revenue_mapping,
        )
        TrialBalanceLine.objects.create(
            financial_year=self.fy,
            account_code="6000",
            account_name="Operating Expenses",
            debit=Decimal("60000"),
            credit=Decimal("0"),
            mapped_line_item=self.expense_mapping,
        )

    def test_tax_provision_missing_fires_for_company_with_no_provision(self):
        """Rule fires when profitable company has no tax provision journal."""
        self._create_profitable_tb()
        from core.risk_engine import run_risk_engine
        results = run_risk_engine(self.fy, tiers=[2])
        rule_ids = [f.rule_id for f in RiskFlag.objects.filter(financial_year=self.fy)]
        self.assertIn("TAX_PROVISION_MISSING", rule_ids)

    def test_tax_provision_missing_does_not_fire_after_journal_posted(self):
        """Rule does NOT fire when a tax_provision journal has been posted."""
        self._create_profitable_tb()
        journal = AdjustingJournal.objects.create(
            financial_year=self.fy,
            description="Income tax provision",
            journal_date=self.fy.end_date,
            created_by=self.admin,
            journal_type="tax_provision",
            status="posted",
        )
        JournalLine.objects.create(
            journal=journal,
            account_code="4110",
            account_name="Income Tax Expense",
            debit=Decimal("10000"),
            credit=Decimal("0"),
        )
        JournalLine.objects.create(
            journal=journal,
            account_code="3325",
            account_name="Provision for Income Tax",
            debit=Decimal("0"),
            credit=Decimal("10000"),
        )
        from core.risk_engine import run_risk_engine
        results = run_risk_engine(self.fy, tiers=[2])
        rule_ids = [f.rule_id for f in RiskFlag.objects.filter(financial_year=self.fy)]
        self.assertNotIn("TAX_PROVISION_MISSING", rule_ids)
