"""Tests for the E2E rig's own configuration and helpers."""
import importlib
import os
from unittest import mock

from django.test import SimpleTestCase


class CacheKeyPrefixTests(SimpleTestCase):
    """Each E2E instance must have its own cache keyspace.

    Instances share redis DB 4 and SESSION_ENGINE is cached_db, so without a
    per-instance prefix a session created against one instance's database can be
    served from cache by another instance whose database never had that session.
    """

    def _load_settings_with(self, **env):
        # settings_e2e refuses to import unless it believes it is talking to an
        # E2E database, so read the module source and evaluate only the CACHES
        # literal rather than importing it.
        import re
        from pathlib import Path

        source = Path("config/settings_e2e.py").read_text()
        match = re.search(r"^CACHES = \{.*?^\}", source, re.S | re.M)
        assert match, "CACHES block not found in config/settings_e2e.py"
        namespace = {"os": os}
        with mock.patch.dict(os.environ, env, clear=False):
            exec(match.group(0), namespace)  # noqa: S102 — evaluating our own settings literal
        return namespace["CACHES"]

    def test_key_prefix_defaults_to_empty(self):
        caches = self._load_settings_with(E2E_CACHE_PREFIX="")
        self.assertEqual(caches["default"].get("KEY_PREFIX", ""), "")

    def test_key_prefix_follows_the_database_name(self):
        caches = self._load_settings_with(E2E_CACHE_PREFIX="sh_e2e_tier2_yearend")
        self.assertEqual(caches["default"]["KEY_PREFIX"], "sh_e2e_tier2_yearend")

    def test_two_instances_get_different_prefixes(self):
        a = self._load_settings_with(E2E_CACHE_PREFIX="sh_e2e_tier2_yearend")
        b = self._load_settings_with(E2E_CACHE_PREFIX="sh_e2e_tier2_rollfwd")
        self.assertNotEqual(a["default"]["KEY_PREFIX"], b["default"]["KEY_PREFIX"])
