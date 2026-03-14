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
ATO_BASE_URL = "https://www.ato.gov.au"
MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")
CACHE_KEY = "review_dashboard_ato_due_dates_v1"
CACHE_TTL_SECONDS = 60 * 60 * 6
MONTH_LINK_RE = re.compile(r"/(july|august|september|october|november|december|january|february|march|april|may|june)-20\d{2}$", re.I)
DATE_HEADING_RE = re.compile(
    r"^(\d{1,2})\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"(?:\s+(20\d{2}))?$",
    re.I,
)


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
            "User-Agent": "StatementHub/1.0 (+https://statementhub.com.au)",
            "Accept-Language": "en-AU,en;q=0.9",
        }
    )

    month_pages = _fetch_month_pages(session)
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

    upcoming.sort(key=lambda item: (item.due_date, item.title.lower()))
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


def _fetch_month_pages(session: requests.Session) -> List[dict]:
    response = session.get(ATO_DUE_DATES_INDEX_URL, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    pages = []
    seen_urls = set()
    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        if not MONTH_LINK_RE.search(href):
            continue
        url = href if href.startswith("http") else f"{ATO_BASE_URL}{href}"
        if url in seen_urls:
            continue
        month_year = _extract_month_year_from_url(url)
        if not month_year:
            continue
        seen_urls.add(url)
        pages.append({"url": url, "month_year": month_year})

    pages.sort(key=lambda item: datetime.strptime(item["month_year"], "%B %Y"))
    return pages


def _parse_month_page(session: requests.Session, url: str, month_year: str) -> List[DueDateSection]:
    response = session.get(url, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

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
        current.descriptions.append(text)

    if current and current.descriptions:
        sections.append(current)
    return sections


def _parse_due_date(text: str, month_year: str) -> date:
    heading_match = DATE_HEADING_RE.match(text)
    if not heading_match:
        raise ValueError(f"Unrecognised due date heading: {text}")
    day = int(heading_match.group(1))
    month_name = heading_match.group(2)
    year = int(heading_match.group(3) or month_year.split()[1])
    return datetime.strptime(f"{day} {month_name} {year}", "%d %B %Y").date()


def _extract_month_year_from_url(url: str) -> str | None:
    slug = url.rstrip("/").split("/")[-1]
    parts = slug.split("-")
    if len(parts) != 2:
        return None
    month = parts[0].capitalize()
    year = parts[1]
    try:
        datetime.strptime(f"{month} {year}", "%B %Y")
    except ValueError:
        return None
    return f"{month} {year}"
