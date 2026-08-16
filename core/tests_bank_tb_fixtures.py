"""Shared fixtures for the bank-statement trial-balance tests.

Six test modules in this project need the same four things: a GST-registered
entity, one or more financial years, a bank mapping, and posted transactions.
Building them inline six times is how the tests drift apart from each other.
"""
from datetime import date
from decimal import Decimal

from core.models import (
    BankAccount, BankAccountMapping, Client, Entity, FinancialYear,
    TrialBalanceLine,
)
from review.models import PendingTransaction, ReviewJob

D = Decimal

# The suite runs without collected staticfiles or a configured object store.
STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def make_entity(name="Desync Test Pty Ltd", gst=True):
    client = Client.objects.create(name=f"{name} Client")
    return Entity.objects.create(
        entity_name=name,
        entity_type="company",
        client=client,
        is_gst_registered=gst,
        bas_frequency="quarterly",
    )


def make_fy(entity, label="FY2026", start=date(2025, 7, 1), end=date(2026, 6, 30)):
    return FinancialYear.objects.create(
        entity=entity, year_label=label, start_date=start, end_date=end,
    )


def make_bank_mapping(entity, code="1100", name="Business Cheque Account"):
    return BankAccountMapping.objects.create(
        entity=entity, bsb="", account_number="", is_default=True,
        tb_account_code=code, tb_account_name=name,
    )


def make_bank_account(entity, bsb, account_number, code, name):
    """A BankAccount with no BankAccountMapping behind it — resolved by
    _get_bank_mapping_for_txn only through its step-5 fallback.
    """
    return BankAccount.objects.create(
        entity=entity, bsb=bsb, account_number=account_number,
        tb_account_code=code, tb_account_name=name,
    )


def make_job(entity, fy):
    return ReviewJob.objects.create(
        entity=entity, financial_year=fy, client_name=entity.entity_name,
        is_gst_registered=entity.is_gst_registered,
    )


def make_txn(job, *, date_str, amount, code, name="", tax_type="", gst="0"):
    """A confirmed but NOT yet posted transaction.

    Post it with core.views._post_txn_to_tb(txn, fy, has_gst) so the test
    exercises the real posting path rather than hand-writing TB rows.
    """
    amount = D(str(amount))
    gst = D(str(gst))
    txn = PendingTransaction.objects.create(
        job=job,
        date=date_str,
        description=f"{code} {date_str}",
        amount=amount,
        confirmed_code=code,
        confirmed_name=name or f"Account {code}",
        confirmed_tax_type=tax_type,
        confirmed_gst_amount=gst,
        gst_amount=gst,
        net_amount=abs(amount) - gst,
        is_confirmed=True,
    )
    return txn


def bs_line(fy, code):
    """The single source='bank_statement' non-adjustment row, or None."""
    return TrialBalanceLine.objects.filter(
        financial_year=fy, account_code=code,
        source="bank_statement", is_adjustment=False,
    ).first()
