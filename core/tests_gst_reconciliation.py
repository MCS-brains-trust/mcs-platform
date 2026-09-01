"""The 3380 control account's two loose ends.

The plan named a ``FinancialStatementLineItem`` model. There is no such model:
the standard line items live in ``AccountMapping``, keyed by ``standard_code``,
and ``ClientAccountMapping.mapped_line_item`` points at one. These tests assert
against the real names.
"""

from decimal import Decimal

from django.test import TestCase

from core.models import (
    AccountMapping, ClientAccountMapping, Entity, EntityChartOfAccount,
)


class GstControlMappingTest(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Mapping Client",
            entity_type=Entity.EntityType.SOLE_TRADER,
            is_gst_registered=True,
        )
        EntityChartOfAccount.objects.create(
            entity=self.entity, account_code="3380",
            account_name="GST payable control account",
            tax_code="", section="liabilities",
        )
        self.item, _ = AccountMapping.objects.get_or_create(
            standard_code="BS-CL-006",
            defaults={
                "line_item_label": "GST payable",
                "financial_statement": AccountMapping.FinancialStatement.BALANCE_SHEET,
                "statement_section": "Current Liabilities",
            },
        )

    def test_seeds_the_bs_cl_006_mapping(self):
        from core.gst_journal import ensure_gst_control_mapping
        ensure_gst_control_mapping(self.entity)
        cam = ClientAccountMapping.objects.get(
            entity=self.entity, client_account_code="3380",
        )
        self.assertIsNotNone(cam.mapped_line_item)
        self.assertEqual(cam.mapped_line_item.standard_code, "BS-CL-006")

    def test_is_idempotent_and_never_overwrites_an_existing_mapping(self):
        from core.gst_journal import ensure_gst_control_mapping
        ensure_gst_control_mapping(self.entity)
        ensure_gst_control_mapping(self.entity)
        self.assertEqual(
            ClientAccountMapping.objects.filter(
                entity=self.entity, client_account_code="3380",
            ).count(),
            1,
        )

    def test_a_deliberate_mapping_is_left_alone(self):
        """The accountant may have pointed 3380 somewhere on purpose."""
        other = AccountMapping.objects.create(
            standard_code="BS-CL-999", line_item_label="Somewhere else",
            financial_statement=AccountMapping.FinancialStatement.BALANCE_SHEET,
            statement_section="Current Liabilities",
        )
        ClientAccountMapping.objects.create(
            entity=self.entity, client_account_code="3380",
            client_account_name="GST payable control account",
            mapped_line_item=other,
        )
        from core.gst_journal import ensure_gst_control_mapping
        ensure_gst_control_mapping(self.entity)
        cam = ClientAccountMapping.objects.get(
            entity=self.entity, client_account_code="3380",
        )
        self.assertEqual(cam.mapped_line_item.standard_code, "BS-CL-999")

    def test_absent_standard_line_item_is_survivable(self):
        """A database without the seed row must not blow up the split."""
        AccountMapping.objects.filter(standard_code="BS-CL-006").delete()
        from core.gst_journal import ensure_gst_control_mapping
        ensure_gst_control_mapping(self.entity)  # must not raise
        self.assertFalse(
            ClientAccountMapping.objects.filter(
                entity=self.entity, client_account_code="3380",
            ).exists()
        )


class EvaGstBucketingTest(TestCase):
    """The control account matched the outer 'gst' filter but neither bucket,
    so the check reported $0.00 both sides while a real balance sat there."""

    def test_control_account_is_bucketed_by_its_columns_not_its_name(self):
        from core.eva_engine import _bucket_gst_account
        collected, paid = _bucket_gst_account(
            account_name="GST payable control account",
            effective_dr=Decimal("418.68"),
            effective_cr=Decimal("2107.91"),
        )
        self.assertEqual(collected, Decimal("2107.91"))
        self.assertEqual(paid, Decimal("418.68"))

    def test_named_accounts_still_bucket_by_name(self):
        from core.eva_engine import _bucket_gst_account
        collected, paid = _bucket_gst_account(
            account_name="GST Collected on Sales",
            effective_dr=Decimal("0"),
            effective_cr=Decimal("500.00"),
        )
        self.assertEqual(collected, Decimal("500.00"))
        self.assertEqual(paid, Decimal("0"))

    def test_an_input_credit_account_still_buckets_by_name(self):
        from core.eva_engine import _bucket_gst_account
        collected, paid = _bucket_gst_account(
            account_name="GST Paid on Purchases",
            effective_dr=Decimal("418.68"),
            effective_cr=Decimal("0"),
        )
        self.assertEqual(collected, Decimal("0"))
        self.assertEqual(paid, Decimal("418.68"))

    def test_a_named_account_nets_its_two_columns(self):
        """A named account carrying both columns is one side's balance, net —
        that was the old behaviour and it stays."""
        from core.eva_engine import _bucket_gst_account
        collected, paid = _bucket_gst_account(
            account_name="GST Collected on Sales",
            effective_dr=Decimal("100.00"),
            effective_cr=Decimal("500.00"),
        )
        self.assertEqual(collected, Decimal("400.00"))
        self.assertEqual(paid, Decimal("0"))
