from __future__ import annotations

import logging
from typing import List

from django.db.models import Q

from core.governing_doc_text import normalize_governing_doc_text, split_governing_doc_sections

logger = logging.getLogger(__name__)

CHUNK_CHAR_LIMIT = 2000
CHUNK_OVERLAP_CHARS = 250
TOP_K_GOVERNING_DOC_CHUNKS = 6


def chunk_governing_document_text(text: str) -> List[dict]:
    """
    Split governing document text into heading-aware chunks suitable for EVA retrieval.
    Falls back to character-window chunking within oversized sections.
    Returns list of dicts with chunk_index, heading, text, start_char, end_char.
    """
    normalized = normalize_governing_doc_text(text)
    if not normalized:
        return []

    sections = split_governing_doc_sections(normalized)
    chunks: List[dict] = []
    chunk_index = 0

    for section in sections:
        section_text = section.text.strip()
        if not section_text:
            continue

        if len(section_text) <= CHUNK_CHAR_LIMIT:
            chunks.append({
                "chunk_index": chunk_index,
                "heading": section.heading,
                "text": section_text,
                "start_char": section.start,
                "end_char": min(section.end, section.start + len(section_text)),
            })
            chunk_index += 1
            continue

        start = 0
        while start < len(section_text):
            end = min(start + CHUNK_CHAR_LIMIT, len(section_text))
            window = section_text[start:end]

            if end < len(section_text):
                split_point = max(
                    window.rfind("\n\n"),
                    window.rfind(". "),
                    window.rfind("; "),
                )
                if split_point > CHUNK_CHAR_LIMIT // 2:
                    end = start + split_point + 1
                    window = section_text[start:end]

            chunks.append({
                "chunk_index": chunk_index,
                "heading": section.heading,
                "text": window.strip(),
                "start_char": section.start + start,
                "end_char": section.start + end,
            })
            chunk_index += 1

            if end >= len(section_text):
                break
            start = max(0, end - CHUNK_OVERLAP_CHARS)

    return chunks


def refresh_governing_document_chunks(governing_document) -> int:
    """
    Rebuild stored retrieval chunks for a governing document from its full extracted text.
    Returns number of stored chunks.
    """
    from core.models import GoverningDocumentChunk

    governing_document.chunks.all().delete()

    chunks = chunk_governing_document_text(governing_document.extracted_text or "")
    if not chunks:
        return 0

    GoverningDocumentChunk.objects.bulk_create([
        GoverningDocumentChunk(
            governing_document=governing_document,
            entity=governing_document.entity,
            chunk_index=item["chunk_index"],
            heading=item["heading"][:255],
            text=item["text"],
            start_char=item["start_char"],
            end_char=item["end_char"],
            token_count=max(1, len(item["text"]) // 4),
        )
        for item in chunks if item["text"].strip()
    ])
    logger.info(
        "Refreshed %d governing document chunks for %s",
        len(chunks), governing_document.pk,
    )
    return len(chunks)


def search_governing_document_chunks(entity, query: str, top_k: int = TOP_K_GOVERNING_DOC_CHUNKS) -> List[dict]:
    """
    Lightweight retrieval over active governing-document chunks for an entity.
    Uses heading/text keyword scoring so EVA can reference large deeds without prompt truncation.
    """
    from core.models import GoverningDocumentChunk

    query = (query or "").strip()
    if not query:
        return []

    terms = [term.lower() for term in query.split() if len(term) >= 3]
    if not terms:
        terms = [query.lower()]

    qs = GoverningDocumentChunk.objects.select_related("governing_document").filter(
        entity=entity,
        governing_document__status="active",
        governing_document__extraction_status__in=["completed", "completed_with_warnings"],
    )

    scored = []
    for chunk in qs.iterator():
        haystack = f"{chunk.heading}\n{chunk.text}".lower()
        score = 0
        for term in terms:
            if term in chunk.heading.lower():
                score += 5
            if term in haystack:
                score += haystack.count(term)
        if score > 0:
            scored.append({
                "chunk_id": str(chunk.id),
                "document_id": str(chunk.governing_document_id),
                "document_title": chunk.governing_document.original_filename or chunk.governing_document.get_document_type_display(),
                "document_type": chunk.governing_document.get_document_type_display(),
                "heading": chunk.heading,
                "text": chunk.text,
                "similarity": float(score),
                "start_char": chunk.start_char,
                "end_char": chunk.end_char,
            })

    scored.sort(key=lambda item: (item["similarity"], -item["start_char"]), reverse=True)
    return scored[:top_k]


def format_governing_document_context(chunks: List[dict]) -> str:
    if not chunks:
        return ""

    lines = ["=== GOVERNING DOCUMENT RETRIEVAL CONTEXT ==="]
    for idx, item in enumerate(chunks, 1):
        lines.extend([
            f"[Governance Source {idx}: {item['document_title']} — {item['heading']}]",
            f"Document Type: {item['document_type']}",
            f"Character Range: {item['start_char']}–{item['end_char']}",
            item['text'],
            "",
        ])
    return "\n".join(lines).strip() + "\n"
