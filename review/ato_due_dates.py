from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import List
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from django.core.cache import cache

ATO_DUE_DATES_INDEX_URL = (
    "https://www.ato.gov.au/tax-and-super-professionals/for-tax-professionals/"
    "prepare-and-lodge/registered-agent-lodgment-program-2025-26/due-dates-by-month"
)
ATO_MONTH_URL_TEMPLATE = (
    "https://www.ato.gov.au/tax-and-super-professionals/for-tax-professionals/"
    "prepare-and-lodge/registered-agent-lodgment-program-2025-26/due-dates-by-month/{slug}"
)
MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")
CACHE_KEY = "review_dashboard_ato_due_dates_v4"
CACHE_TTL_SECONDS = 60 * 60 * 6
DATE_HEADING_RE = re.compile(
    r"^(\d{1,2})\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"(?:\s+(20\d{2}))?$",
    re.I,
)

FALLBACK_MONTH_CONTENT = {
    "march-2026": """
    <main>
        <h2>21 March</h2>
        <p>Lodge and pay February 2026 monthly business activity statement.</p>
        <h2>31 March</h2>
        <p>Lodge tax return for companies and super funds with total income of more than $2 million in the latest year lodged (excluding large and medium taxpayers), unless the return was due earlier.</p>
        <p>Payment for companies and super funds in this category is also due by this date.</p>
        <p>Lodge tax return for the head company of a consolidated group (excluding large and medium), with a member who had a total income in excess of $2 million in their latest year lodged, unless the return was due earlier.</p>
        <p>Payment for companies in this category is also due by this date.</p>
        <p>Lodge tax return for individuals and trusts whose latest return resulted in a tax liability of $20,000 or more, excluding large and medium trusts.</p>
        <p>Payment for individuals and trusts in this category is due as advised on their notice of assessment.</p>
    </main>
    """,
    "april-2026": """
    <main>
        <h2>21 April</h2>
        <p>Lodge and pay March 2026 monthly business activity statement.</p>
        <h2>28 April</h2>
        <p>Lodge and pay quarter 3, 2025-26 activity statement for clients lodging by paper.</p>
        <h2>30 April</h2>
        <p>Lodge and pay quarter 3, 2025-26 activity statement for clients lodging electronically.</p>
        <p>Lodge and pay quarter 3, 2025-26 instalment activity statement for quarter 3 payers.</p>
    </main>
    """,
    "may-2026": """
    <main>
        <h2>15 May</h2>
        <p>Lodge 2025 tax returns for all entities with a lodgment due date of 15 May 2026, provided all previously due returns for the client were lodged by 31 October 2025 and both the latest return and current year return are not excluded from the lodgment program.</p>
        <p>Payment for tax returns in this category is due as advised on the notice of assessment.</p>
        <h2>21 May</h2>
        <p>Lodge and pay April 2026 monthly business activity statement.</p>
    </main>
    """,
    "june-2026": """
    <main>
        <h2>5 June</h2>
        <p>Lodge tax returns due for individuals and trusts with a lodgment due date of 15 May 2026 if the return is not lodged electronically.</p>
        <h2>15 June</h2>
        <p>Lodge tax returns due for companies and super funds with a lodgment due date of 15 May 2026 if the return is not lodged electronically.</p>
        <h2>21 June</h2>
        <p>Lodge and pay May 2026 monthly business activity statement.</p>
        <h2>25 June</h2>
        <p>Lodge and pay quarter 3, 2025-26 activity statements for all electronic lodgers if the client has a quarterly lodgment and payment concession.</p>
    </main>
    """,
}


@dataclass
class DueDateItem:
    due_date: date
    title: str
    description: str
    source_url: str


@dataclass
class DueDateSection:
    title: str
    due_date: date
    source_url: str
    descriptions: List[str]


def get_next_ato_due_dates(limit: int = 3) -> List[dict]:
    cached = cache.get(CACHE_KEY)
    if cached:
        return cached[:limit]

    today = datetime.now(MELBOURNE_TZ).date()
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-AU,en;q=0.9",
            "Referer": "https://www.google.com/",
            "Upgrade-Insecure-Requests": "1",
        }
    )

    month_pages = _build_fallback_month_pages(today)
    upcoming: List[DueDateItem] = []

    for page in month_pages:
        sections = _parse_month_page(session, page["url"], page["month_year"])
        for section in sections:
            if section.due_date < today:
                continue
            combined_description = " ".join(section.descriptions).strip()
            upcoming.append(
                DueDateItem(
                    due_date=section.due_date,
                    title=section.title,
                    description=combined_description,
                    source_url=section.source_url,
                )
            )
        if len(upcoming) >= limit + 3:
            break

    upcoming.sort(key=lambda item: (item.due_date, item.title.lower(), item.description.lower()))
    result = [
        {
            "due_date": item.due_date,
            "title": item.title,
            "description": item.description,
            "source_url": item.source_url,
        }
        for item in upcoming[:limit]
    ]
    cache.set(CACHE_KEY, result, CACHE_TTL_SECONDS)
    return result


def _build_fallback_month_pages(today: date) -> List[dict]:
    months = []
    current = date(today.year, today.month, 1)

    for _ in range(12):
        if current.year == 2025 and current.month < 7:
            pass
        elif current.year > 2026 or (current.year == 2026 and current.month > 6):
            break
        else:
            month_year = current.strftime("%B %Y")
            slug = _slug_from_month_year(month_year)
            months.append(
                {
                    "url": ATO_MONTH_URL_TEMPLATE.format(slug=slug),
                    "month_year": month_year,
                }
            )

        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)

    return months


def _parse_month_page(session: requests.Session, url: str, month_year: str) -> List[DueDateSection]:
    html = _get_month_page_html(session, url, month_year)
    soup = BeautifulSoup(html, "html.parser")

    article = soup.find("main") or soup
    sections: List[DueDateSection] = []
    current: DueDateSection | None = None

    for tag in article.find_all(["h2", "h3", "p", "li"]):
        text = " ".join(tag.get_text(" ", strip=True).split())
        if not text:
            continue

        heading_match = DATE_HEADING_RE.match(text)
        if tag.name in {"h2", "h3"} and heading_match:
            if current and current.descriptions:
                sections.append(current)
            current = DueDateSection(
                title=text,
                due_date=_parse_due_date(text, month_year),
                source_url=url,
                descriptions=[],
            )
            continue

        if current is None:
            continue

        lowered = text.lower()
        if lowered in {"print or download", "on this page", "back to list of due dates"}:
            continue
        if DATE_HEADING_RE.match(text):
            continue
        if text not in current.descriptions:
            current.descriptions.append(text)

    if current and current.descriptions:
        sections.append(current)
    return sections


def _get_month_page_html(session: requests.Session, url: str, month_year: str) -> str:
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
        return response.text
    except requests.RequestException:
        slug = _slug_from_month_year(month_year)
        fallback = FALLBACK_MONTH_CONTENT.get(slug)
        if fallback:
            return fallback
        raise


def _parse_due_date(text: str, month_year: str) -> date:
    heading_match = DATE_HEADING_RE.match(text)
    if not heading_match:
        raise ValueError(f"Unrecognised due date heading: {text}")
    day = int(heading_match.group(1))
    month_name = heading_match.group(2)
    year = int(heading_match.group(3) or month_year.split()[1])
    return datetime.strptime(f"{day} {month_name} {year}", "%d %B %Y").date()


def _slug_from_month_year(month_year: str) -> str:
    return month_year.lower().replace(" ", "-")
