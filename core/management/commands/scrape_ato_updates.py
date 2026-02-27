"""
Management command: scrape_ato_updates

Scrapes the ATO "What's New" page (via Coveo API) and the ATO Legal Database
"What's New" page, generates a structured Markdown document, and saves it
directly into Eva's Knowledge Brain database (KnowledgeDocument + chunks).

Designed to run on the StatementHub VPS as a daily cron job.

Usage:
    python manage.py scrape_ato_updates
    python manage.py scrape_ato_updates --dry-run   # scrape only, save to /tmp/
    python manage.py scrape_ato_updates --no-embed   # save to DB without embeddings

Cron (daily at 7:00 AM AEST):
    0 7 * * * cd /opt/statementhub && source venv/bin/activate && \
    python manage.py scrape_ato_updates >> /var/log/ato_scrape.log 2>&1
"""
import logging
import re
from collections import OrderedDict
from datetime import date, datetime

import requests as http_requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────
# ATO source URLs
# ──────────────────────────────────────────────────────────
ATO_WHATS_NEW_URL = "https://www.ato.gov.au/whats-new"
ATO_LEGAL_DB_URL = "https://www.ato.gov.au/law/view/whatsnew.htm"
ATO_NEW_LEGISLATION_URL = (
    "https://www.ato.gov.au/about-ato/new-legislation/"
    "latest-news-on-tax-law-and-policy"
)

# Coveo search API (used by the ATO What's New React SPA)
COVEO_API_URL = (
    "https://australiantaxationofficeproductionfe8uurdl.org.coveo.com"
    "/rest/search/v2"
    "?organizationId=australiantaxationofficeproductionfe8uurdl"
)
COVEO_TOKEN = "xxf77fcd30-975f-4235-a06f-e4c3d369e6d6"

# Knowledge Brain category for ATO updates
KB_CATEGORY = "tax_legislation"

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
        "and save to Eva Knowledge Brain"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Scrape and generate the document but do not save to DB",
        )
        parser.add_argument(
            "--no-embed",
            action="store_true",
            help="Save to DB but skip generating embeddings (faster)",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        no_embed = options.get("no_embed", False)
        self.stdout.write("=" * 60)
        self.stdout.write("ATO Update Scraper — Starting")
        self.stdout.write("=" * 60)

        # Create a persistent session for cookie handling
        self.session = http_requests.Session()
        self.session.headers.update(BROWSER_HEADERS)

        # 1. Scrape both sources
        legal_db_items = self._scrape_legal_database()
        whats_new_items = self._scrape_whats_new_coveo()

        if not legal_db_items and not whats_new_items:
            self.stdout.write(self.style.WARNING(
                "No items scraped from either source. "
                "The ATO may be blocking this server's IP, "
                "or the Coveo API token may have expired."
            ))
            return

        self.stdout.write(
            f"Scraped {len(legal_db_items)} Legal Database items, "
            f"{len(whats_new_items)} What's New items."
        )

        # 2. Generate Markdown document
        today = date.today()
        markdown = self._generate_markdown(today, legal_db_items, whats_new_items)
        filename = f"ATO_Updates_{today.strftime('%Y-%m-%d')}"

        # 3. Save
        if dry_run:
            local_path = f"/tmp/{filename}.md"
            with open(local_path, "w") as f:
                f.write(markdown)
            self.stdout.write(self.style.SUCCESS(
                f"[DRY RUN] Document saved locally: {local_path}"
            ))
            self.stdout.write(f"Document size: {len(markdown)} bytes")
            return

        # Save directly to Eva Knowledge Brain database
        self._save_to_knowledge_brain(filename, markdown, no_embed)

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
    # Scraper: ATO What's New (via Coveo search API)
    # ──────────────────────────────────────────────────────────
    def _scrape_whats_new_coveo(self):
        """
        Fetches ATO What's New items via the Coveo search API.
        The ATO website is a Next.js React SPA that uses Coveo for search.
        Direct HTML scraping returns an empty shell, so we call the same
        API the frontend uses.

        Returns a list of dicts with keys:
            date, title, url, description
        """
        self.stdout.write("Scraping ATO What's New (via Coveo API)...")
        items = []

        payload = {
            "locale": "en-US",
            "debug": False,
            "tab": "default",
            "timezone": "UTC",
            "context": {"database": "web", "language": "en"},
            "fieldsToInclude": [
                "dateupdated", "description", "pagetitle", "quickcode",
            ],
            "q": "",
            "enableQuerySyntax": False,
            "searchHub": "ATOGov WhatsNew",
            "sortCriteria": "@dateupdated descending",
            "numberOfResults": 100,
            "firstResult": 0,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {COVEO_TOKEN}",
        }

        try:
            resp = http_requests.post(
                COVEO_API_URL,
                json=payload,
                headers=headers,
                timeout=30,
            )
            if resp.status_code != 200:
                self.stdout.write(self.style.WARNING(
                    f"Coveo API returned HTTP {resp.status_code}"
                ))
                # Fall back to HTML scraping
                return self._scrape_whats_new_html_fallback()

            data = resp.json()
            results = data.get("results", [])

            for r in results:
                raw = r.get("raw", {})

                # Parse the epoch timestamp
                date_epoch = raw.get("dateupdated")
                date_text = ""
                if date_epoch:
                    try:
                        dt = datetime.fromtimestamp(date_epoch / 1000)
                        date_text = dt.strftime("%d %B %Y")
                    except (ValueError, TypeError, OSError):
                        pass

                # Title: remove " | Australian Taxation Office" suffix
                title = r.get("title", "")
                title = re.sub(
                    r"\s*\|\s*Australian Taxation Office\s*$", "", title
                )

                url = r.get("clickUri", "")
                description = raw.get("description", "")

                if title:
                    items.append({
                        "date": date_text,
                        "title": title,
                        "url": url,
                        "description": description,
                    })

        except Exception as e:
            logger.error(f"Coveo API failed: {e}")
            self.stdout.write(self.style.WARNING(
                f"Coveo API failed: {e}. Trying HTML fallback..."
            ))
            return self._scrape_whats_new_html_fallback()

        self.stdout.write(f"  Found {len(items)} items from What's New")
        return items

    def _scrape_whats_new_html_fallback(self):
        """
        Fallback HTML scraper for ATO What's New.
        Only works if the server IP isn't blocked by Akamai and the
        page renders server-side (unlikely for the React SPA).
        """
        self.stdout.write("  Trying HTML fallback for What's New...")
        items = []

        try:
            resp = self.session.get(ATO_WHATS_NEW_URL, timeout=30)
            if resp.status_code != 200:
                return items
        except Exception:
            return items

        soup = BeautifulSoup(resp.text, "html.parser")

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

            parent = article_link.parent
            desc_p = parent.find("p") if parent else None
            description = desc_p.get_text(strip=True) if desc_p else ""

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

        self.stdout.write(
            f"  HTML fallback found {len(items)} items"
        )
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
    # Save directly to Eva Knowledge Brain database
    # ──────────────────────────────────────────────────────────
    def _save_to_knowledge_brain(self, title, markdown, no_embed=False):
        """
        Save the scraped ATO update document directly into the
        KnowledgeDocument / KnowledgeChunk tables, bypassing SharePoint.

        This uses the same chunking and embedding pipeline as the
        sync_knowledge_brain() function in eva_service.py.
        """
        try:
            from core.models import KnowledgeDocument, KnowledgeChunk
            from core.models import AuditLog
            from core.eva_service import chunk_text, get_embedding
        except ImportError as e:
            self.stdout.write(self.style.ERROR(
                f"Failed to import Knowledge Brain models: {e}"
            ))
            return

        today_str = date.today().strftime("%Y-%m-%d")
        sharepoint_path = (
            f"02_Tax_Legislation/ATO_Rulings/{title}.md"
        )

        # Check if today's document already exists (idempotent)
        existing = KnowledgeDocument.objects.filter(
            sharepoint_path=sharepoint_path,
        ).first()

        if existing:
            self.stdout.write(
                f"Updating existing document: {existing.title}"
            )
            doc = existing
            doc.title = title
            doc.sync_status = KnowledgeDocument.SyncStatus.PENDING
            doc.save()
            # Delete old chunks
            doc.chunks.all().delete()
        else:
            doc = KnowledgeDocument.objects.create(
                title=title,
                category=KB_CATEGORY,
                sharepoint_path=sharepoint_path,
                sharepoint_item_id=f"ato-scrape-{today_str}",
                sharepoint_modified_at=timezone.now().isoformat(),
                file_type="md",
                file_size_bytes=len(markdown.encode("utf-8")),
            )
            self.stdout.write(f"Created new document: {title}")

        # Chunk the text
        chunks = chunk_text(markdown)
        self.stdout.write(f"  Generated {len(chunks)} chunks")

        # Embed and store chunks
        embedded_count = 0
        for chunk_data in chunks:
            embedding = None
            if not no_embed:
                try:
                    embedding = get_embedding(chunk_data["text"])
                    embedded_count += 1
                except Exception as e:
                    logger.error(
                        f"Embedding failed for chunk "
                        f"{chunk_data['chunk_index']}: {e}"
                    )

            KnowledgeChunk.objects.create(
                document=doc,
                chunk_index=chunk_data["chunk_index"],
                text=chunk_data["text"],
                embedding=embedding,
                token_count=chunk_data["token_count"],
            )

        # Finalise the document
        doc.chunk_count = len(chunks)
        doc.sync_status = KnowledgeDocument.SyncStatus.SYNCED
        doc.synced_at = timezone.now()
        doc.save()

        # Audit log
        try:
            AuditLog.objects.create(
                action=AuditLog.Action.EVA_SYNC,
                description=(
                    f"ATO Update scraper saved '{title}' to "
                    f"Knowledge Brain. {len(chunks)} chunks, "
                    f"{embedded_count} embedded."
                ),
                metadata={
                    "source": "scrape_ato_updates",
                    "document_id": str(doc.pk),
                    "chunks": len(chunks),
                    "embedded": embedded_count,
                },
            )
        except Exception:
            pass

        self.stdout.write(self.style.SUCCESS(
            f"Saved to Knowledge Brain: {title} "
            f"({len(chunks)} chunks, {embedded_count} embedded)"
        ))
