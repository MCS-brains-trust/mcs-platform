"""
StatementHub — Firm Settings Views

Provides the in-app Firm Settings admin page (accessible from the sidebar
Administration section) where admins can upload the firm logo, set the firm
name, contact details, and other branding fields.

This is separate from the Django admin interface and uses the same Bootstrap 5
UI as the rest of the platform.
"""
import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core.models import FirmSettings


# ---------------------------------------------------------------------------
# Firm Settings — view + update
# ---------------------------------------------------------------------------

@login_required
@require_http_methods(["GET", "POST"])
def firm_settings(request):
    """Display and update the firm branding settings."""
    if not request.user.is_admin:
        messages.error(request, "Only administrators can manage firm settings.")
        return redirect("core:entity_list")

    settings_obj = FirmSettings.get()

    if request.method == "POST":
        # ── Text fields ──────────────────────────────────────────────────
        settings_obj.firm_name = request.POST.get("firm_name", "").strip()
        settings_obj.firm_legal_name = request.POST.get("firm_legal_name", "").strip()
        settings_obj.firm_abn = request.POST.get("firm_abn", "").strip()
        settings_obj.firm_address_1 = request.POST.get("firm_address_1", "").strip()
        settings_obj.firm_address_2 = request.POST.get("firm_address_2", "").strip()
        settings_obj.firm_phone = request.POST.get("firm_phone", "").strip()
        settings_obj.firm_email = request.POST.get("firm_email", "").strip()
        settings_obj.firm_website = request.POST.get("firm_website", "").strip()
        settings_obj.compilation_report_name = request.POST.get("compilation_report_name", "").strip()
        settings_obj.document_disclaimer = request.POST.get("document_disclaimer", "").strip()

        # ── Logo upload ───────────────────────────────────────────────────
        if "logo" in request.FILES:
            logo_file = request.FILES["logo"]

            # Basic validation
            allowed_types = {"image/png", "image/jpeg", "image/jpg", "image/svg+xml"}
            if logo_file.content_type not in allowed_types:
                messages.error(
                    request,
                    "Invalid file type. Please upload a PNG, JPEG, or SVG image.",
                )
                return render(request, "core/firm_settings.html", {"settings": settings_obj})

            max_size_mb = 2
            if logo_file.size > max_size_mb * 1024 * 1024:
                messages.error(
                    request,
                    f"Logo file is too large. Maximum size is {max_size_mb} MB.",
                )
                return render(request, "core/firm_settings.html", {"settings": settings_obj})

            # Delete old logo file from disk to avoid orphaned files
            if settings_obj.logo:
                try:
                    old_path = settings_obj.logo.path
                    if os.path.exists(old_path):
                        os.remove(old_path)
                except Exception:
                    pass  # Non-fatal — old file cleanup is best-effort

            settings_obj.logo = logo_file

        # ── Logo removal ──────────────────────────────────────────────────
        elif request.POST.get("remove_logo") == "1" and settings_obj.logo:
            try:
                old_path = settings_obj.logo.path
                if os.path.exists(old_path):
                    os.remove(old_path)
            except Exception:
                pass
            settings_obj.logo = None

        settings_obj.updated_by = request.user
        settings_obj.save()

        messages.success(request, "Firm settings saved successfully.")
        return redirect("core:firm_settings")

    context = {
        "settings": settings_obj,
        "page_title": "Firm Settings",
    }
    return render(request, "core/firm_settings.html", context)
