"""
The firm logo must reach financial statements at render time.

Historically the logo was embedded into the cover .docx when the template
was *generated* (generate_fs_templates._build_cover), so a new upload in
Firm Settings never reached client financial statements until someone
re-ran the generator with --force.
"""
import io
import os
import zipfile

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from core.models import FinancialStatementTemplate, FirmSettings


def _png_bytes(colour):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (120, 60), colour).save(buf, format="PNG")
    return buf.getvalue()


def _embedded_images(docx_bytes):
    z = zipfile.ZipFile(io.BytesIO(docx_bytes))
    return [z.read(n) for n in z.namelist() if n.startswith("word/media/")]


def _upload_logo(colour, filename):
    """Put a logo of `colour` in Firm Settings; return its raw bytes."""
    raw = _png_bytes(colour)
    firm = FirmSettings.get()
    firm.logo = SimpleUploadedFile(filename, raw, content_type="image/png")
    firm.save()
    return raw


def _static_logo_bytes():
    with open(os.path.join(settings.BASE_DIR, "static", "MCSlogo.png"), "rb") as fh:
        return fh.read()


class CoverTemplateLogoPlaceholderTests(TestCase):
    """The generated template must carry a merge field, not a picture."""

    def test_cover_template_has_logo_merge_field(self):
        from core.management.commands.generate_fs_templates import _build_cover
        doc = _build_cover("company")
        self.assertIn("{{ practice_logo }}", [p.text for p in doc.paragraphs])

    def test_cover_template_has_no_embedded_image(self):
        from core.management.commands.generate_fs_templates import _build_cover
        doc = _build_cover("company")
        self.assertEqual(len(doc.inline_shapes), 0)


class RenderTimeLogoInjectionTests(TestCase):
    """render_template resolves the logo from FirmSettings on every render."""

    def _cover_record(self):
        """Generate and register a cover template from the code as it stands."""
        from core.management.commands.generate_fs_templates import _build_cover
        relative_path = "fs_templates/test/COVER_company.docx"
        full_path = os.path.join(settings.MEDIA_ROOT, relative_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        _build_cover("company").save(full_path)
        return FinancialStatementTemplate.objects.create(
            name="Cover Page — Company",
            document_type="COVER",
            entity_type="company",
            template_file=relative_path,
            is_active=False,  # migrations already seeded the active COVER/company row
        )

    def _render(self, record):
        from core.fs_template_service import render_template
        return _embedded_images(
            render_template(record, {"entity_name": "Test Pty Ltd"}).getvalue()
        )

    def test_logo_uploaded_after_template_generation_is_embedded(self):
        record = self._cover_record()
        new_logo = _upload_logo((10, 20, 30), "new_brand.png")

        self.assertIn(new_logo, self._render(record))

    def test_superseded_logo_is_not_embedded(self):
        old_logo = _upload_logo((200, 30, 30), "old_brand.png")
        record = self._cover_record()
        _upload_logo((10, 20, 30), "replacement_brand.png")

        self.assertNotIn(old_logo, self._render(record))

    def test_static_logo_used_when_firm_has_no_upload(self):
        _upload_logo((200, 30, 30), "removed_brand.png")
        record = self._cover_record()
        firm = FirmSettings.get()
        firm.logo = None
        firm.save()

        self.assertIn(_static_logo_bytes(), self._render(record))
