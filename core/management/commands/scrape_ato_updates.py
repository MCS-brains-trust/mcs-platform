"""
Management command: scrape_ato_updates

Scrapes the ATO "What's New" page and the ATO Legal Database "What's New"
page, generates a structured Markdown document, and uploads it to the
Eva Knowledge Brain SharePoint library under 02_Tax_Legislation/ATO_Rulings.

Designed to run on the StatementHub VPS (Australian IP) as a daily cron job.

Usage:
    python manage.py scrape_ato_updates
    python manage.py scrape_ato_updates --dry-run   # scrape only, no upload
    python manage.py scrape_ato_updates --no-sync    # upload but skip KB sync

Cron (daily at 7:00 AM AEST):
    0 7 * * * cd /opt/statementhub && source venv/bin/activate && python manage.py scrape_ato_updates >> /var/log/ato_scrape.log 2>&1
"""
import os
import logging
import re
from collections import OrderedDict
from datetime import date

import requests as http_requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# ATO source URLs
# ──────────────────────────────────────────────────────────────
ATO_WHATS_NEW_URL = "https://www.ato.gov.au/whats-new"
ATO_LEGAL_DB_URL = "https://www.ato.gov.au/law/view/whatsnew.htm"
ATO_NEW_LEGISLATION_URL = (
    "https://www.ato.gov.au/about-ato/new-legislation/"
    "latest-news-on-tax-law-and-policy"
)

# SharePoint target folder inside the Eva Knowledge Brain library
SHAREPOINT_UPLOAD_FOLDER = "02_Tax_Legislation/ATO_Rulings"

# Browser-like headers (the VPS's Australian IP should pass Akamai)
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-AU,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}


class Command(BaseCommand):
    help = (
        "Scrape ATO announcements (Legal Database + What's New) "
        "and upload to Eva Knowledge Brain on SharePoint"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Scrape and generate the document but do not upload to SharePoint",
        )
        parser.add_argument(
            "--no-sync",
            action="store_true",
            help="Upload to SharePoint but skip triggering a Knowledge Brain sync",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        no_sync = options.get("no_sync", False)
        self.stdout.write("=" * 60)
        self.stdout.write("ATO Update Scraper — Starting")
        self.stdout.write("=" * 60)

        # Create a persistent session for cookie handling
        self.session = http_requests.Session()
        self.session.headers.update(BROWSER_HEADERS)

        # 1. Scrape both sources
        legal_db_items = self._scrape_legal_database()
        whats_new_items = self._scrape_whats_new()

        if not legal_db_items and not whats_new_items:
            self.stdout.write(self.style.WARNING(
                "No items scraped from either source. "
                "The ATO may be blocking this server's IP."
            ))
            return

        self.stdout.write(
            f"Scraped {len(legal_db_items)} Legal Database items, "
            f"{len(whats_new_items)} What's New items."
        )

        # 2. Generate Markdown document
        today = date.today()
        markdown = self._generate_markdown(today, legal_db_items, whats_new_items)

        # 3. Upload to SharePoint (or save locally for dry-run)
        filename = f"ATO_Updates_{today.strftime('%Y-%m-%d')}.md"

        if dry_run:
            local_path = f"/tmp/{filename}"
            with open(local_path, "w") as f:
                f.write(markdown)
            self.stdout.write(self.style.SUCCESS(
                f"[DRY RUN] Document saved locally: {local_path}"
            ))
            self.stdout.write(f"Document size: {len(markdown)} bytes")
            return

        success = self._upload_to_sharepoint(filename, markdown)
        if success:
            self.stdout.write(self.style.SUCCESS(
                f"Uploaded {filename} to SharePoint "
                f"({SHAREPOINT_UPLOAD_FOLDER})"
            ))
            if not no_sync:
                self._trigger_sync()
        else:
            self.stdout.write(self.style.ERROR(
                "Failed to upload to SharePoint. Check logs."
            ))

    # ──────────────────────────────────────────────────────────
    # Scraper: ATO Legal Database What's New
    # ──────────────────────────────────────────────────────────
    def _scrape_legal_database(self):
        """
        Scrapes https://www.ato.gov.au/law/view/whatsnew.htm

        Returns a list of dicts with keys:
            date, category, title, url, description
        """
        self.stdout.write("Scraping ATO Legal Database What's New...")
        items = []

        try:
            resp = self.session.get(ATO_LEGAL_DB_URL, timeout=30)
            if resp.status_code != 200:
                self.stdout.write(self.style.WARNING(
                    f"Legal Database returned HTTP {resp.status_code}"
                ))
                return items
        except Exception as e:
            logger.error(f"Failed to fetch ATO Legal Database: {e}")
            self.stdout.write(self.style.ERROR(f"Request failed: {e}"))
            return items

        soup = BeautifulSoup(resp.text, "html.parser")

        # Check for access denied
        title_tag = soup.find("title")
        if title_tag and "access denied" in title_tag.text.lower():
            self.stdout.write(self.style.WARNING(
                "ATO Legal Database returned 'Access Denied'. "
                "The server IP may be blocked by Akamai."
            ))
            return items

        current_date = ""
        current_category = ""

        for element in soup.find_all(["h2", "h3", "tr"]):
            tag = element.name

            if tag == "h2":
                text = element.get_text(strip=True)
                if re.match(r"\d{1,2}\s+\w+\s+\d{4}", text):
                    current_date = text

            elif tag == "h3":
                current_category = element.get_text(strip=True)

            elif tag == "tr":
                link = element.find("a")
                if not link:
                    continue

                title = link.get_text(strip=True)
                if not title:
                    continue

                href = link.get("href", "")
                if href and not href.startswith("http"):
                    # Legal DB uses relative URLs like /law/view/document?DocID=...
                    href = f"https://www.ato.gov.au{href}"

                # Extract description from adjacent table cell
                tds = element.find_all("td")
                description = ""
                if len(tds) >= 2:
                    description = tds[1].get_text(strip=True)
                elif len(tds) == 1:
                    full_text = tds[0].get_text(strip=True)
                    if title in full_text:
                        description = full_text.replace(title, "").strip()

                items.append({
                    "date": current_date,
                    "category": current_category,
                    "title": title,
                    "url": href,
                    "description": description,
                })

        self.stdout.write(f"  Found {len(items)} items from Legal Database")
        return items

    # ──────────────────────────────────────────────────────────
    # Scraper: ATO What's New (general announcements)
    # ──────────────────────────────────────────────────────────
    def _scrape_whats_new(self):
        """
        Scrapes https://www.ato.gov.au/whats-new

        Returns a list of dicts with keys:
            date, title, url, description
        """
        self.stdout.write("Scraping ATO What's New...")
        items = []

        try:
            resp = self.session.get(ATO_WHATS_NEW_URL, timeout=30)
            if resp.status_code != 200:
                self.stdout.write(self.style.WARNING(
                    f"What's New returned HTTP {resp.status_code}"
                ))
                return items
        except Exception as e:
            logger.error(f"Failed to fetch ATO What's New: {e}")
            self.stdout.write(self.style.ERROR(f"Request failed: {e}"))
            return items

        soup = BeautifulSoup(resp.text, "html.parser")

        # Check for access denied
        title_tag = soup.find("title")
        if title_tag and "access denied" in title_tag.text.lower():
            self.stdout.write(self.style.WARNING(
                "ATO What's New returned 'Access Denied'. "
                "The server IP may be blocked by Akamai."
            ))
            return items

        # The What's New page uses article-like cards with h2 titles
        for article_link in soup.find_all("a", href=True):
            h2 = article_link.find("h2")
            if not h2:
                continue

            title = h2.get_text(strip=True)
            if not title:
                continue

            href = article_link.get("href", "")
            if href and not href.startswith("http"):
                href = f"https://www.ato.gov.au{href}"

            # Find the description paragraph
            parent = article_link.parent
            desc_p = parent.find("p") if parent else None
            description = desc_p.get_text(strip=True) if desc_p else ""

            # Find the date
            date_text = ""
            if parent:
                for text_node in parent.stripped_strings:
                    if re.match(r"\d{1,2}\s+\w+\s+\d{4}", text_node):
                        date_text = text_node
                        break

            items.append({
                "date": date_text,
                "title": title,
                "url": href,
                "description": description,
            })

        self.stdout.write(f"  Found {len(items)} items from What's New")
        return items

    # ──────────────────────────────────────────────────────────
    # Markdown document generator
    # ──────────────────────────────────────────────────────────
    def _generate_markdown(self, today, legal_db_items, whats_new_items):
        """Generate a structured Markdown document from scraped items."""
        lines = [
            f"# ATO Updates — {today.strftime('%d %B %Y')}",
            "",
            (
                f"*Automatically scraped on "
                f"{today.strftime('%d %B %Y')} by StatementHub.*"
            ),
            "",
            "This document contains the latest ATO announcements, rulings, "
            "determinations, practice statements, legislative instruments, "
            "and other updates relevant to tax practitioners.",
            "",
            "---",
            "",
        ]

        # ── Section 1: Legal Database ──
        if legal_db_items:
            lines.append(
                "## ATO Legal Database — New Rulings, "
                "Determinations & Instruments"
            )
            lines.append("")

            # Group by date
            by_date = OrderedDict()
            for item in legal_db_items:
                d = item["date"] or "Undated"
                by_date.setdefault(d, []).append(item)

            for date_str, date_items in by_date.items():
                lines.append(f"### {date_str}")
                lines.append("")

                # Group by category within each date
                by_cat = OrderedDict()
                for item in date_items:
                    cat = item["category"] or "Other"
                    by_cat.setdefault(cat, []).append(item)

                for cat, cat_items in by_cat.items():
                    lines.append(f"**{cat}**")
                    lines.append("")
                    for item in cat_items:
                        desc = (
                            f" — {item['description']}"
                            if item["description"] else ""
                        )
                        if item["url"]:
                            lines.append(
                                f"- [{item['title']}]({item['url']}){desc}"
                            )
                        else:
                            lines.append(f"- {item['title']}{desc}")
                    lines.append("")

            lines.append("---")
            lines.append("")

        # ── Section 2: ATO What's New ──
        if whats_new_items:
            lines.append("## ATO General Announcements")
            lines.append("")

            for item in whats_new_items:
                date_str = (
                    f" ({item['date']})" if item["date"] else ""
                )
                desc = (
                    f": {item['description']}" if item["description"] else ""
                )
                if item["url"]:
                    lines.append(
                        f"- [{item['title']}]({item['url']})"
                        f"{date_str}{desc}"
                    )
                else:
                    lines.append(
                        f"- {item['title']}{date_str}{desc}"
                    )

            lines.append("")

        # ── Footer ──
        lines.extend([
            "---",
            "",
            "**Sources:**",
            f"- [ATO Legal Database — What's New]({ATO_LEGAL_DB_URL})",
            f"- [ATO What's New]({ATO_WHATS_NEW_URL})",
            (
                f"- [Latest News on Tax Law & Policy]"
                f"({ATO_NEW_LEGISLATION_URL})"
            ),
            "",
            (
                f"*Document generated: "
                f"{timezone.now().strftime('%Y-%m-%d %H:%M:%S %Z')}*"
            ),
            "",
        ])

        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────
    # SharePoint upload via Microsoft Graph API
    # ──────────────────────────────────────────────────────────
    def _upload_to_sharepoint(self, filename, content):
        """
        Upload a file to the Eva Knowledge Brain SharePoint library.
        Uses the same credentials as the existing sync_knowledge_brain().
        """
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
            logger.error("SharePoint credentials not configured.")
            self.stdout.write(self.style.ERROR(
                "SharePoint credentials not configured. "
                "Set SHAREPOINT_TENANT_ID, SHAREPOINT_CLIENT_ID, "
                "SHAREPOINT_CLIENT_SECRET."
            ))
            return False

        if not site_id and not site_url:
            logger.error("SharePoint site not configured.")
            self.stdout.write(self.style.ERROR(
                "Set either SHAREPOINT_SITE_ID or SHAREPOINT_SITE_URL."
            ))
            return False

        try:
            # Get OAuth2 token
            token_url = (
                f"https://login.microsoftonline.com/"
                f"{tenant_id}/oauth2/v2.0/token"
            )
            token_data = {
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "https://graph.microsoft.com/.default",
            }
            token_resp = http_requests.post(token_url, data=token_data)
            token_resp.raise_for_status()
            access_token = token_resp.json()["access_token"]
            graph_base = "https://graph.microsoft.com/v1.0"

            auth_headers = {"Authorization": f"Bearer {access_token}"}

            # Resolve site_id if needed
            if not site_id:
                from urllib.parse import urlparse

                parsed = urlparse(site_url)
                hostname = parsed.hostname
                site_path = parsed.path.rstrip("/")
                resolve_url = (
                    f"{graph_base}/sites/{hostname}:{site_path}"
                )
                site_resp = http_requests.get(
                    resolve_url, headers=auth_headers
                )
                site_resp.raise_for_status()
                site_id = site_resp.json()["id"]

            # Resolve drive_id if needed
            if not drive_id:
                drives_url = f"{graph_base}/sites/{site_id}/drives"
                drives_resp = http_requests.get(
                    drives_url, headers=auth_headers
                )
                drives_resp.raise_for_status()
                for drv in drives_resp.json().get("value", []):
                    if drv.get("name") == library_name:
                        drive_id = drv["id"]
                        break
                if not drive_id:
                    logger.error(
                        f"Drive not found for library '{library_name}'"
                    )
                    return False

            # Upload the file (simple upload API, < 4MB)
            upload_path = f"{SHAREPOINT_UPLOAD_FOLDER}/{filename}"
            upload_url = (
                f"{graph_base}/sites/{site_id}/drives/{drive_id}"
                f"/root:/{upload_path}:/content"
            )

            upload_headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/octet-stream",
            }
            file_bytes = content.encode("utf-8")
            upload_resp = http_requests.put(
                upload_url, headers=upload_headers, data=file_bytes
            )
            upload_resp.raise_for_status()

            web_url = upload_resp.json().get("webUrl", "unknown")
            logger.info(f"Uploaded {filename} to SharePoint: {web_url}")
            self.stdout.write(f"SharePoint URL: {web_url}")
            return True

        except Exception as e:
            logger.error(f"SharePoint upload failed: {e}")
            self.stdout.write(self.style.ERROR(
                f"SharePoint upload failed: {e}"
            ))
            return False

    # ──────────────────────────────────────────────────────────
    # Trigger Knowledge Brain sync after upload
    # ──────────────────────────────────────────────────────────
    def _trigger_sync(self):
        """Trigger a Knowledge Brain sync so Eva picks up the new document."""
        try:
            from core.eva_service import sync_knowledge_brain

            self.stdout.write("Triggering Eva Knowledge Brain sync...")
            stats = sync_knowledge_brain()
            self.stdout.write(self.style.SUCCESS(
                f"Sync complete: {stats}"
            ))
        except Exception as e:
            logger.error(f"Knowledge Brain sync failed: {e}")
            self.stdout.write(self.style.WARNING(
                f"Upload succeeded but sync failed: {e}. "
                "The document will be picked up on the next "
                "scheduled sync."
            ))
