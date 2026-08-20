"""The endpoint the industry dropdown fetches its search index from.

Also the place the committed fixture itself is checked: a regenerated index
whose codes drift from the 582 official ones would otherwise only show up as a
dropdown entry the form refuses to validate.
"""
import json

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from accounts.oidc_views import ENTRA_SESSION_KEY
from core.industry_activities import ACTIVITY_INDEX
from core.industry_codes import INDUSTRY_CODE_MAP


class IndustryActivitiesEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="ross", email="ross@mcands.com.au", password="x" * 14,
            role=User.Role.ADMIN,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session[ENTRA_SESSION_KEY] = True
        session["2fa_verified"] = True
        session.save()

    def test_serves_the_activity_index(self):
        response = self.client.get(reverse("core:industry_activities"), secure=True)
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertEqual(body["11110"], ACTIVITY_INDEX["11110"])

    def test_the_index_is_cacheable_so_it_is_fetched_once(self):
        response = self.client.get(reverse("core:industry_activities"), secure=True)
        self.assertIn("max-age", response["Cache-Control"])

    def test_anonymous_callers_are_refused(self):
        self.client.logout()
        response = self.client.get(reverse("core:industry_activities"), secure=True)
        self.assertNotEqual(response.status_code, 200)


class CommittedActivityFixtureTests(TestCase):
    """Data tests: these fail on a bad regeneration, not on a bad request."""

    def test_the_fixture_was_generated(self):
        self.assertGreater(len(ACTIVITY_INDEX), 500)

    def test_every_code_in_the_index_is_an_official_bic(self):
        unknown = sorted(set(ACTIVITY_INDEX) - set(INDUSTRY_CODE_MAP))
        self.assertEqual(unknown, [])

    def test_every_official_code_has_at_least_one_activity(self):
        """582 codes, 582 covered: a code with no activity is unsearchable."""
        uncovered = sorted(set(INDUSTRY_CODE_MAP) - set(ACTIVITY_INDEX))
        self.assertEqual(uncovered, [])

    def test_the_activities_people_actually_search_for_resolve(self):
        """The specific searches that motivated this: none are official labels."""
        def code_for(activity):
            hits = [c for c, acts in ACTIVITY_INDEX.items()
                    if any(activity in a.lower() for a in acts)]
            return sorted(hits)
        self.assertIn("11110", code_for("abattoir"))
        self.assertIn("69320", code_for("bookkeeping"))
        self.assertIn("95110", code_for("hairdressing"))
