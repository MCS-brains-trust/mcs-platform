# core/tests_jt_identity.py
"""JT is the source of truth for identity, and is allowed to be down.

Three states, mirroring CoWorker's chat/jt_jobs.py contract, which is the
established pattern for JT calls in this stack:

  ok           JT answered; `fields` carries JT's typed-absence envelope
  not_found    JT answered 404: no client with that XPM id
  unavailable  timeout, 403, 5xx, malformed body — anything else

The distinction matters at the template: `not_found` means "this link is wrong",
`unavailable` means "we could not ask". Neither may block statement work.
"""
from unittest.mock import patch

import requests
from django.test import TestCase, override_settings

from core import jt_identity

XPM_ID = "aaaaaaaa-0000-0000-0000-000000000009"

OK_BODY = {
    "xpmId": XPM_ID,
    "fields": {
        "legalName": {"status": "held", "value": "Example Holdings Pty Ltd"},
        "entityType": {"status": "held", "value": "Company"},
        "abn": {"status": "held", "value": "11222333444"},
        "tfn": {"status": "restricted", "masked": "***-***-789"},
        "address": {"status": "held", "value": "1 Example Street"},
        "dateOfBirth": {"status": "not_held"},
    },
}


class FakeResponse:
    def __init__(self, status_code, payload=None, raise_on_json=False):
        self.status_code = status_code
        self._payload = payload
        self._raise_on_json = raise_on_json

    def json(self):
        if self._raise_on_json:
            raise ValueError("not json")
        return self._payload


@override_settings(
    JT_INTERNAL_BASE_URL="https://jobtracker.example/job-tracker/api/internal",
    JT_INTERNAL_SERVICE_SECRET="test-secret",
)
class FetchIdentityTests(TestCase):
    def test_success_returns_ok_and_exposes_held_values(self):
        with patch.object(jt_identity.requests, "get", return_value=FakeResponse(200, OK_BODY)) as get:
            result = jt_identity.fetch_identity(XPM_ID)
        self.assertEqual(result.state, "ok")
        self.assertEqual(result.held("legalName"), "Example Holdings Pty Ltd")
        self.assertEqual(result.held("abn"), "11222333444")
        # not_held and restricted are not values
        self.assertIsNone(result.held("dateOfBirth"))
        self.assertIsNone(result.held("tfn"))
        # The service secret goes in the header, and PII is never requested.
        _, kwargs = get.call_args
        self.assertEqual(kwargs["headers"]["x-service-auth"], "test-secret")
        self.assertNotIn("includePii", kwargs.get("params", {}))

    def test_404_is_not_found_not_unavailable(self):
        with patch.object(jt_identity.requests, "get",
                          return_value=FakeResponse(404, {"error": "client_not_found"})):
            result = jt_identity.fetch_identity(XPM_ID)
        self.assertEqual(result.state, "not_found")
        self.assertEqual(result.fields, {})

    def test_403_is_unavailable(self):
        # The likely real-world cause: SH's egress IP fell out of JT's nginx
        # allow-list after a droplet rebuild.
        with patch.object(jt_identity.requests, "get", return_value=FakeResponse(403)):
            self.assertEqual(jt_identity.fetch_identity(XPM_ID).state, "unavailable")

    def test_timeout_is_unavailable(self):
        with patch.object(jt_identity.requests, "get", side_effect=requests.exceptions.Timeout):
            self.assertEqual(jt_identity.fetch_identity(XPM_ID).state, "unavailable")

    def test_connection_error_is_unavailable(self):
        with patch.object(jt_identity.requests, "get",
                          side_effect=requests.exceptions.ConnectionError):
            self.assertEqual(jt_identity.fetch_identity(XPM_ID).state, "unavailable")

    def test_malformed_json_is_unavailable(self):
        with patch.object(jt_identity.requests, "get",
                          return_value=FakeResponse(200, raise_on_json=True)):
            self.assertEqual(jt_identity.fetch_identity(XPM_ID).state, "unavailable")

    def test_missing_fields_envelope_is_unavailable(self):
        with patch.object(jt_identity.requests, "get",
                          return_value=FakeResponse(200, {"xpmId": XPM_ID})):
            self.assertEqual(jt_identity.fetch_identity(XPM_ID).state, "unavailable")

    def test_blank_xpm_id_never_calls_jt(self):
        with patch.object(jt_identity.requests, "get") as get:
            result = jt_identity.fetch_identity("  ")
        self.assertEqual(result.state, "not_found")
        get.assert_not_called()

    def test_a_timeout_is_bounded(self):
        with patch.object(jt_identity.requests, "get", return_value=FakeResponse(200, OK_BODY)) as get:
            jt_identity.fetch_identity(XPM_ID)
        self.assertLessEqual(get.call_args.kwargs["timeout"], 10)


@override_settings(
    JT_INTERNAL_BASE_URL="https://jobtracker.example/job-tracker/api/internal",
    JT_INTERNAL_SERVICE_SECRET="test-secret",
)
class SearchClientsTests(TestCase):
    def test_success_returns_rows(self):
        body = {"clients": [
            {"xpmId": "xpm-1", "displayName": "Example Holdings Pty Ltd",
             "entityType": "Company", "abn": "11222333444"},
        ], "limit": 10}
        with patch.object(jt_identity.requests, "get", return_value=FakeResponse(200, body)):
            result = jt_identity.search_clients("example")
        self.assertFalse(result.failed)
        self.assertEqual(result.clients[0]["displayName"], "Example Holdings Pty Ltd")

    def test_short_query_returns_empty_without_calling_jt(self):
        with patch.object(jt_identity.requests, "get") as get:
            result = jt_identity.search_clients("e")
        self.assertFalse(result.failed)
        self.assertEqual(result.clients, [])
        get.assert_not_called()

    def test_error_is_failed_with_no_rows(self):
        with patch.object(jt_identity.requests, "get", side_effect=requests.exceptions.Timeout):
            result = jt_identity.search_clients("example")
        self.assertTrue(result.failed)
        self.assertEqual(result.clients, [])

    def test_limit_is_passed_through_and_capped_locally(self):
        with patch.object(jt_identity.requests, "get",
                          return_value=FakeResponse(200, {"clients": []})) as get:
            jt_identity.search_clients("example", limit=5000)
        self.assertEqual(get.call_args.kwargs["params"]["limit"], 25)
