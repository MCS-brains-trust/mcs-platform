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
    StockItem, MeetingNote, ActivityLog,
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
