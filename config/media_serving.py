"""
Secure media file serving.

All media files (generated financial statements, templates, uploads) are
served through Django views with authentication and authorization checks,
rather than being exposed directly via the web server.
"""
import mimetypes
import os

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404


@login_required
def serve_protected_media(request, path):
    """
    Serve media files with authentication required.
    Only logged-in users can access media files.
    """
    file_path = os.path.join(settings.MEDIA_ROOT, path)

    # Prevent path traversal attacks
    real_path = os.path.realpath(file_path)
    real_media_root = os.path.realpath(settings.MEDIA_ROOT)
    if not real_path.startswith(real_media_root):
        raise Http404

    if not os.path.exists(real_path) or not os.path.isfile(real_path):
        raise Http404

    content_type, _ = mimetypes.guess_type(real_path)
    response = FileResponse(
        open(real_path, "rb"),
        content_type=content_type or "application/octet-stream",
    )

    # Force download for ALL served media files (security finding B8). Serving
    # any uploaded file inline lets an attacker upload .html/.svg that would
    # execute script in the app origin (stored XSS). Forcing attachment makes
    # the browser download rather than render it, closing that path.
    filename = os.path.basename(real_path)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    # Security headers for served files
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-cache, no-store"

    return response
