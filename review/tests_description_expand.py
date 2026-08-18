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
