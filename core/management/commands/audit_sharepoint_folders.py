"""
Phase 1.4 — read-only audit of SharePoint folder mapping.

Walks every folder in SHAREPOINT_FOLDER_MAP via Microsoft Graph and reports:
  - whether the folder exists,
  - recursive file count (excluding `archive` subfolders),
  - most recent lastModifiedDateTime across all files in the folder tree.

Writes nothing to the database. To be removed (or moved to _archive/) in
Phase 4 — see specs/eva_kb_phase2_wave1_spec.md section 2.4.

Usage (server only):
    source /opt/statementhub/venv/bin/activate && cd /opt/statementhub && \\
    python3 manage.py audit_sharepoint_folders
"""
import os
from urllib.parse import urlparse

import requests
from django.core.management.base import BaseCommand, CommandError

from core.eva_service import SHAREPOINT_FOLDER_MAP


GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SUPPORTED_EXTS = ("docx", "pdf", "txt", "xlsx", "pptx", "msg")


class Command(BaseCommand):
    help = (
        "Read-only walk of every folder declared in SHAREPOINT_FOLDER_MAP. "
        "Prints folder path, existence, recursive file count (excluding archive "
        "subfolders), and the most recent lastModifiedDateTime per folder."
    )

    def handle(self, *args, **options):
        tenant_id = os.environ.get("SHAREPOINT_TENANT_ID", "")
        client_id = os.environ.get("SHAREPOINT_CLIENT_ID", "")
        client_secret = os.environ.get("SHAREPOINT_CLIENT_SECRET", "")
        site_url = os.environ.get("SHAREPOINT_SITE_URL", "")
        library_name = os.environ.get(
            "SHAREPOINT_LIBRARY_NAME", "Eva Knowledge Brain"
        )
        site_id = os.environ.get("SHAREPOINT_SITE_ID", "")
        drive_id = os.environ.get("SHAREPOINT_DRIVE_ID", "")

        if not all([tenant_id, client_id, client_secret]):
            raise CommandError(
                "SharePoint credentials not configured "
                "(SHAREPOINT_TENANT_ID/CLIENT_ID/CLIENT_SECRET)."
            )
        if not site_id and not site_url:
            raise CommandError(
                "Set SHAREPOINT_SITE_ID or SHAREPOINT_SITE_URL."
            )

        token = self._get_token(tenant_id, client_id, client_secret)
        headers = {"Authorization": f"Bearer {token}"}

        if not site_id:
            site_id = self._resolve_site_id(site_url, headers)
        if not drive_id:
            drive_id = self._resolve_drive_id(site_id, library_name, headers)

        rows = []
        for folder_path, category in SHAREPOINT_FOLDER_MAP.items():
            exists, file_count, latest = self._walk_folder(
                site_id, drive_id, folder_path, headers
            )
            rows.append({
                "folder": folder_path,
                "category": category,
                "exists": exists,
                "files": file_count,
                "latest_modified": latest or "",
            })

        # Print a compact, terminal-friendly table.
        self.stdout.write("")
        self.stdout.write(
            f"{'FOLDER':<55} {'CATEGORY':<24} {'EXISTS':<7} "
            f"{'FILES':>6}  LATEST MODIFIED"
        )
        self.stdout.write("-" * 130)
        for r in rows:
            self.stdout.write(
                f"{r['folder']:<55} {r['category']:<24} "
                f"{'yes' if r['exists'] else 'NO':<7} "
                f"{r['files']:>6}  {r['latest_modified']}"
            )
        self.stdout.write("")

        total_files = sum(r["files"] for r in rows if r["exists"])
        missing = [r["folder"] for r in rows if not r["exists"]]
        empty = [
            r["folder"] for r in rows
            if r["exists"] and r["files"] == 0
        ]
        self.stdout.write(
            f"Summary: {len(rows)} folders mapped | "
            f"{len(rows) - len(missing)} exist | {len(missing)} missing | "
            f"{len(empty)} empty | {total_files} files total "
            f"(archive subfolders excluded)."
        )
        if missing:
            self.stdout.write(f"Missing: {missing}")
        if empty:
            self.stdout.write(f"Empty:   {empty}")

    # ------------------------------------------------------------------
    # Graph helpers (mirror eva_service.sync_knowledge_brain auth path)
    # ------------------------------------------------------------------
    def _get_token(self, tenant_id, client_id, client_secret):
        token_url = (
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        )
        resp = requests.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def _resolve_site_id(self, site_url, headers):
        parsed = urlparse(site_url)
        hostname = parsed.hostname
        site_path = parsed.path.rstrip("/")
        resolve_url = f"{GRAPH_BASE}/sites/{hostname}:{site_path}"
        resp = requests.get(resolve_url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()["id"]

    def _resolve_drive_id(self, site_id, library_name, headers):
        drives_url = f"{GRAPH_BASE}/sites/{site_id}/drives"
        resp = requests.get(drives_url, headers=headers, timeout=30)
        resp.raise_for_status()
        for drv in resp.json().get("value", []):
            if drv.get("name") == library_name:
                return drv["id"]
        raise CommandError(
            f"Drive not found for library '{library_name}'. "
            f"Available: {[d['name'] for d in resp.json().get('value', [])]}"
        )

    def _walk_folder(self, site_id, drive_id, folder_path, headers):
        """Recursively walk a folder and return (exists, file_count, latest_iso).

        Skips any subfolder whose name contains 'archive' (case-insensitive),
        matching the production sync's archive-skip behaviour.
        Only counts files with extensions in SUPPORTED_EXTS, matching what the
        production sync would actually ingest.
        """
        list_url = (
            f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}"
            f"/root:/{folder_path}:/children"
        )
        try:
            resp = requests.get(list_url, headers=headers, timeout=30)
        except requests.RequestException as exc:
            self.stderr.write(f"  request error on {folder_path}: {exc}")
            return False, 0, None

        if resp.status_code == 404:
            return False, 0, None
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            self.stderr.write(f"  HTTP error on {folder_path}: {exc}")
            return False, 0, None

        items = resp.json().get("value", [])
        file_count = 0
        latest = None

        for item in items:
            if item.get("folder"):
                if "archive" in item["name"].lower():
                    continue
                sub_path = f"{folder_path}/{item['name']}"
                _, sub_count, sub_latest = self._walk_folder(
                    site_id, drive_id, sub_path, headers
                )
                file_count += sub_count
                if sub_latest and (latest is None or sub_latest > latest):
                    latest = sub_latest
                continue

            name = item.get("name", "")
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext not in SUPPORTED_EXTS:
                continue
            file_count += 1
            modified = item.get("lastModifiedDateTime", "")
            if modified and (latest is None or modified > latest):
                latest = modified

        return True, file_count, latest
