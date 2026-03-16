from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List


DEFAULT_CHAT_SECTION_LIMIT = 5
DEFAULT_SECTION_CHAR_LIMIT = 4000
DEFAULT_TOTAL_CHAR_LIMIT = 18000


@dataclass
class GoverningDocSection:
    heading: str
    text: str
    start: int
    end: int


def normalize_governing_doc_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x00", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_HEADING_PATTERNS: List[re.Pattern] = [
    re.compile(r"^(schedule|appendix|annexure)\b.*", re.IGNORECASE),
    re.compile(r"^(clause|section|part)\s+[0-9A-Za-z.\-]+\b.*", re.IGNORECASE),
    re.compile(r"^[0-9]+(?:\.[0-9A-Za-z]+){0,4}\s+.+$"),
    re.compile(r"^[A-Z][A-Z\s,&()'\-/]{5,}$"),
]


def _looks_like_heading(line: str) -> bool:
    candidate = line.strip()
    if len(candidate) < 4:
        return False
    if len(candidate) > 180:
        return False
    return any(pattern.match(candidate) for pattern in _HEADING_PATTERNS)


def split_governing_doc_sections(text: str) -> List[GoverningDocSection]:
    normalized = normalize_governing_doc_text(text)
    if not normalized:
        return []

    lines = normalized.split("\n")
    sections: List[GoverningDocSection] = []
    current_heading = "Document Start"
    current_lines: List[str] = []
    cursor = 0
    section_start = 0

    def flush_section(end_cursor: int) -> None:
        nonlocal current_lines, current_heading, section_start
        body = "\n".join(current_lines).strip()
        if body:
            sections.append(
                GoverningDocSection(
                    heading=current_heading,
                    text=body,
                    start=section_start,
                    end=end_cursor,
                )
            )
        current_lines = []

    for raw_line in lines:
        line = raw_line.rstrip()
        line_len = len(raw_line) + 1
        if _looks_like_heading(line):
            flush_section(cursor)
            current_heading = line.strip()
            section_start = cursor
        else:
            current_lines.append(raw_line)
        cursor += line_len

    flush_section(len(normalized))

    if not sections:
        return [
            GoverningDocSection(
                heading="Document Text",
                text=normalized,
                start=0,
                end=len(normalized),
            )
        ]

    return sections


def build_chat_excerpt(
    text: str,
    max_sections: int = DEFAULT_CHAT_SECTION_LIMIT,
    max_section_chars: int = DEFAULT_SECTION_CHAR_LIMIT,
    max_total_chars: int = DEFAULT_TOTAL_CHAR_LIMIT,
) -> str:
    sections = split_governing_doc_sections(text)
    if not sections:
        return ""

    excerpt_parts: List[str] = []
    total_chars = 0
    truncated = False

    for section in sections[:max_sections]:
        body = section.text.strip()
        if len(body) > max_section_chars:
            body = body[:max_section_chars].rstrip() + "\n[Section excerpt truncated]"
            truncated = True
        part = f"## {section.heading}\n{body}".strip()
        if total_chars + len(part) > max_total_chars:
            remaining = max_total_chars - total_chars
            if remaining > 200:
                excerpt_parts.append(part[:remaining].rstrip() + "\n[Document excerpt truncated]")
            truncated = True
            break
        excerpt_parts.append(part)
        total_chars += len(part) + 2

    if len(sections) > max_sections:
        truncated = True

    if truncated:
        excerpt_parts.append(
            "[Large governing document preserved in full storage; only structured excerpts are shown in chat context.]"
        )

    return "\n\n".join(excerpt_parts)
