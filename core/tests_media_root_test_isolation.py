"""
Migrations 0103/0106/0107 rebuild the FS templates by writing .docx files
into settings.MEDIA_ROOT.  Under `manage.py test` that used to be the live
media directory, so any test run overwrote the real client-facing templates
(with a blank FirmSettings, i.e. no uploaded logo).
"""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class MediaRootTestIsolationTests(SimpleTestCase):
    def test_testing_flag_is_set_during_test_runs(self):
        self.assertTrue(getattr(settings, "TESTING", False))

    def test_media_root_is_not_the_live_media_directory(self):
        live_media = (Path(settings.BASE_DIR) / "media").resolve()
        self.assertNotEqual(Path(settings.MEDIA_ROOT).resolve(), live_media)
