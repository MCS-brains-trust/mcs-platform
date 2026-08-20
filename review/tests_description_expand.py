"""The review list must carry each transaction's complete description.

The description is clamped to two lines and expanded by clicking, which is a
CSS and JavaScript concern — but it only works because the whole string is in
the HTML to begin with. A ``truncatechars`` filter added later would break the
expand silently: the row would still expand, just to the same shortened text.
This is the guard against that.

Scanned statements are what make this matter. Vision OCR returns descriptions
like the ANZ one below, long enough to wrap several lines in a column sharing
the table with nine others.
"""
from django.test import TestCase, override_settings
from django.urls import reverse

from core.tests_bank_tb_fixtures import (
    STORAGES_OVERRIDE, make_bank_mapping, make_entity, make_fy, make_job,
)

from .models import PendingTransaction

LONG_DESCRIPTION = (
    "EFTPOS NP-CARDINIA CLUB \\PAKENHAM03 AU EFFECTIVE DATE 26 APR 2026"
)


@override_settings(STORAGES=STORAGES_OVERRIDE)
class DescriptionReachesThePageTests(TestCase):

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)
        self.txn = PendingTransaction.objects.create(
            job=self.job,
            date="2026-04-27",
            description=LONG_DESCRIPTION,
            amount="-202.90",
        )

        User = get_user_model()
        self.user = User.objects.create_user(
            username="desc-expand", password="pw", email="d@example.com",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-desc", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

    def _get(self):
        return self.client.get(
            reverse("review:review_detail", args=[self.job.pk]), secure=True)

    def test_the_whole_description_is_rendered_not_a_shortened_one(self):
        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "EFFECTIVE DATE 26 APR 2026")

    def test_the_description_cell_is_marked_as_expandable(self):
        # The class is what the stylesheet clamps and the click handler binds
        # to; without it the description renders but never expands.
        response = self._get()

        self.assertContains(response, "txn-description")


@override_settings(STORAGES=STORAGES_OVERRIDE)
class DescriptionReachesTheReviewTabTests(TestCase):
    """The same guard for the Review tab inside a financial year.

    This tab allocates the transactions from a bank statement upload, and until
    now it cut each description to eight words server-side, which put the rest
    of the string out of reach of any amount of CSS or clicking. Both of its
    tables — pending and confirmed — must carry the whole thing.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.entity = make_entity()
        self.fy = make_fy(self.entity)
        make_bank_mapping(self.entity)
        self.job = make_job(self.entity, self.fy)
        self.pending = PendingTransaction.objects.create(
            job=self.job,
            date="2026-04-27",
            description=LONG_DESCRIPTION,
            amount="-202.90",
        )
        self.confirmed = PendingTransaction.objects.create(
            job=self.job,
            date="2026-04-28",
            description=LONG_DESCRIPTION,
            amount="-303.90",
            is_confirmed=True,
        )

        User = get_user_model()
        self.user = User.objects.create_user(
            username="tab-expand", password="pw", email="t@example.com",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-tab", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

    def _get(self):
        return self.client.get(
            reverse("core:financial_year_detail", args=[self.fy.pk]),
            secure=True)

    def test_the_whole_description_is_rendered_not_a_shortened_one(self):
        response = self._get()

        self.assertEqual(response.status_code, 200)
        # Asserting on the cell markup, not just the string: the row's
        # data-description attribute has always held the full description, so
        # a bare substring check passes even while the visible cell is cut to
        # eight words. The pending and confirmed tables each render one span.
        self.assertContains(
            response,
            '<span class="txn-description">%s</span>' % LONG_DESCRIPTION,
            count=2,
        )

    def test_the_clamp_and_its_click_target_are_wired_up(self):
        # The class is what the stylesheet clamps and the delegated click
        # handler binds to; without it the description renders in full and
        # every row becomes a different height.
        response = self._get()

        self.assertContains(response, "-webkit-line-clamp")
        self.assertContains(response, "txn-description")
