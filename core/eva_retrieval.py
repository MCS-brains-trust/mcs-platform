"""
Eva Hybrid Retrieval Engine
============================
Replaces the pure pgvector cosine-similarity search in eva_service.py with
a three-stage hybrid pipeline:

  Stage 1 — Vector Search:   pgvector cosine distance on KnowledgeChunk.embedding_vector
  Stage 2 — Keyword Search:  PostgreSQL Full Text Search on KnowledgeChunk.text (tsvector)
  Stage 3 — Lesson Boost:    EvaLearnedLesson records injected with priority_weight boost
  Stage 4 — Rerank:          Claude Haiku scores each candidate against the query
                              and returns the final top-K list

The hybrid approach is significantly more accurate than pure vector search for
queries that contain specific account codes, ABNs, dollar amounts, or proper nouns
(e.g. "What is the benchmark rate for Division 7A in FY2025?").

Usage:
    from core.eva_retrieval import hybrid_search

    results = hybrid_search(
        query="Division 7A benchmark interest rate FY2025",
        user=request.user,          # optional — for lesson personalisation
        entity=financial_year.entity,  # optional — for entity-specific lessons
        top_k=8,
    )
    # returns list of RetrievalResult dicts
"""

import json
import logging
import math
from typing import Optional

from django.db import connection
from django.conf import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VECTOR_CANDIDATES = 20       # How many chunks to pull from pgvector stage
KEYWORD_CANDIDATES = 20      # How many chunks to pull from FTS stage
LESSON_CANDIDATES = 10       # Max lessons to inject
DEFAULT_TOP_K = 8            # Final result count after reranking
RERANK_ENABLED = True        # Set False to skip reranker (faster, less accurate)
MIN_VECTOR_SIMILARITY = 0.25 # Discard chunks below this similarity threshold


# ---------------------------------------------------------------------------
# Stage 1 — Vector Search
# ---------------------------------------------------------------------------
def _vector_search(query_embedding: list, top_k: int, category_filter: Optional[str] = None) -> list:
    """
    Search KnowledgeChunk using pgvector cosine distance.
    Returns list of candidate dicts.
    """
    from core.models import KnowledgeChunk, KnowledgeDocument

    try:
        from pgvector.django import CosineDistance
    except ImportError:
        logger.warning("pgvector not available — skipping vector search stage")
        return []

    qs = KnowledgeChunk.objects.filter(
        document__is_archived=False,
        document__sync_status=KnowledgeDocument.SyncStatus.SYNCED,
        embedding_vector__isnull=False,
    ).select_related("document").annotate(
        distance=CosineDistance("embedding_vector", query_embedding),
    ).order_by("distance")

    if category_filter:
        qs = qs.filter(document__category=category_filter)

    results = []
    for chunk in qs[:top_k]:
        similarity = 1.0 - float(chunk.distance or 0.0)
        if similarity < MIN_VECTOR_SIMILARITY:
            continue
        results.append({
            "chunk_id": str(chunk.id),
            "text": chunk.text,
            "document_title": chunk.document.title,
            "document_id": str(chunk.document.id),
            "category": chunk.document.get_category_display(),
            "source": "vector",
            "raw_score": similarity,
            "final_score": similarity,
        })
    return results


# ---------------------------------------------------------------------------
# Stage 2 — Full Text Search
# ---------------------------------------------------------------------------
def _keyword_search(query: str, top_k: int, category_filter: Optional[str] = None) -> list:
    """
    Search KnowledgeChunk using PostgreSQL Full Text Search.
    Falls back gracefully on non-PostgreSQL backends (e.g. SQLite in tests).
    """
    from core.models import KnowledgeChunk

    # Sanitise query for tsquery — strip special chars, join with &
    words = [w for w in query.split() if len(w) >= 2]
    if not words:
        return []

    # Build a plainto_tsquery-compatible string
    ts_query = " & ".join(words[:20])  # cap at 20 terms

    try:
        with connection.cursor() as cursor:
            # Check if tsvector column exists; if not, fall back to ILIKE
            cursor.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'core_knowledgechunk'
                  AND column_name = 'text_search_vector'
            """)
            has_tsvector = cursor.fetchone() is not None

        if has_tsvector:
            sql = """
                SELECT
                    ck.id,
                    ck.text,
                    kd.title,
                    kd.id as doc_id,
                    kd.category,
                    ts_rank(ck.text_search_vector, plainto_tsquery('english', %s)) AS rank
                FROM core_knowledgechunk ck
                JOIN core_knowledgedocument kd ON kd.id = ck.document_id
                WHERE kd.is_archived = false
                  AND ck.text_search_vector @@ plainto_tsquery('english', %s)
                {category_clause}
                ORDER BY rank DESC
                LIMIT %s
            """.format(
                category_clause="AND kd.category = %s" if category_filter else ""
            )
            params = [query, query]
            if category_filter:
                params.append(category_filter)
            params.append(top_k)
        else:
            # Fallback: ILIKE search (slower but always works)
            like_terms = [f"%{w}%" for w in words[:5]]
            conditions = " AND ".join(["ck.text ILIKE %s"] * len(like_terms))
            sql = f"""
                SELECT
                    ck.id,
                    ck.text,
                    kd.title,
                    kd.id as doc_id,
                    kd.category,
                    1.0 AS rank
                FROM core_knowledgechunk ck
                JOIN core_knowledgedocument kd ON kd.id = ck.document_id
                WHERE kd.is_archived = false
                  AND ({conditions})
                ORDER BY ck.id
                LIMIT %s
            """
            params = like_terms + [top_k]

        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()

    except Exception as e:
        logger.warning(f"FTS search failed: {e}")
        return []

    results = []
    for row in rows:
        chunk_id, text, doc_title, doc_id, category, rank = row
        results.append({
            "chunk_id": str(chunk_id),
            "text": text,
            "document_title": doc_title,
            "document_id": str(doc_id),
            "category": category or "",
            "source": "keyword",
            "raw_score": float(rank),
            "final_score": float(rank) * 0.8,  # slight discount vs vector
        })
    return results


# ---------------------------------------------------------------------------
# Stage 3 — Learned Lesson Injection
# ---------------------------------------------------------------------------
def _lesson_search(query_embedding: list, user=None, entity=None, top_k: int = LESSON_CANDIDATES) -> list:
    """
    Retrieve relevant EvaLearnedLesson records and inject them as high-priority candidates.
    Lessons are scored by cosine similarity then boosted by their priority_weight.
    """
    from core.models import EvaLearnedLesson

    qs = EvaLearnedLesson.objects.filter(is_active=True)

    # Personalise: prefer lessons from this user or this entity
    if user:
        qs = qs.filter(
            source_user=user
        ) | EvaLearnedLesson.objects.filter(is_active=True, source_user__isnull=True)
    if entity:
        qs = qs.filter(
            source_entity=entity
        ) | EvaLearnedLesson.objects.filter(is_active=True, source_entity__isnull=True)

    # Deduplicate
    qs = qs.distinct()

    results = []
    for lesson in qs.order_by("-priority_weight")[:top_k * 3]:
        if not lesson.embedding_vector:
            continue
        try:
            vec = lesson.embedding_vector
            if isinstance(vec, str):
                vec = json.loads(vec)
            sim = _cosine_similarity(query_embedding, vec)
        except Exception:
            sim = 0.0

        if sim < 0.2:
            continue

        boosted_score = sim * lesson.priority_weight
        results.append({
            "chunk_id": f"lesson:{lesson.id}",
            "text": lesson.lesson_text,
            "document_title": f"Learned Lesson [{lesson.get_category_display()}]",
            "document_id": str(lesson.id),
            "category": lesson.get_category_display(),
            "source": "lesson",
            "raw_score": sim,
            "final_score": boosted_score,
        })

    results.sort(key=lambda x: x["final_score"], reverse=True)
    return results[:top_k]


# ---------------------------------------------------------------------------
# Stage 4 — Claude Haiku Reranker
# ---------------------------------------------------------------------------
def _rerank(query: str, candidates: list, top_k: int) -> list:
    """
    Use Claude Haiku to score each candidate's relevance to the query.
    Returns the top_k candidates sorted by rerank score.

    Falls back to score-based sorting if the LLM call fails.
    """
    if not RERANK_ENABLED or len(candidates) <= top_k:
        candidates.sort(key=lambda x: x["final_score"], reverse=True)
        return candidates[:top_k]

    from core.ai_service import _call_llm

    # Build a compact representation of each candidate
    candidate_list = []
    for i, c in enumerate(candidates):
        candidate_list.append(f"[{i}] {c['document_title']}: {c['text'][:300]}")

    candidates_text = "\n\n".join(candidate_list)

    system_prompt = (
        "You are a relevance scoring assistant for an accounting AI system. "
        "Given a query and a list of text candidates, score each candidate's "
        "relevance to the query on a scale of 0.0 to 1.0. "
        "Return ONLY a JSON array of numbers, one per candidate, in the same order. "
        "Example: [0.9, 0.3, 0.7, 0.1]"
    )
    user_prompt = f"Query: {query}\n\nCandidates:\n{candidates_text}"

    try:
        response = _call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tier="haiku",
            temperature=0.0,
            max_tokens=200,
        )
        # Parse the response
        resp_text = response if isinstance(response, str) else response.get("content", "")
        # Extract JSON array
        import re
        match = re.search(r'\[[\d.,\s]+\]', resp_text)
        if match:
            scores = json.loads(match.group())
            if len(scores) == len(candidates):
                for i, score in enumerate(scores):
                    candidates[i]["rerank_score"] = float(score)
                    candidates[i]["final_score"] = float(score)
                candidates.sort(key=lambda x: x["final_score"], reverse=True)
                return candidates[:top_k]
    except Exception as e:
        logger.warning(f"Reranker failed, falling back to score sort: {e}")

    # Fallback
    candidates.sort(key=lambda x: x["final_score"], reverse=True)
    return candidates[:top_k]


# ---------------------------------------------------------------------------
# Merge & Deduplicate
# ---------------------------------------------------------------------------
def _merge_candidates(vector_results: list, keyword_results: list, lesson_results: list) -> list:
    """
    Merge results from all three stages, deduplicating by chunk_id.
    When a chunk appears in both vector and keyword results, keep the higher score.
    """
    seen = {}

    for result in vector_results + keyword_results + lesson_results:
        chunk_id = result["chunk_id"]
        if chunk_id not in seen:
            seen[chunk_id] = result
        else:
            # Keep the version with the higher final_score
            if result["final_score"] > seen[chunk_id]["final_score"]:
                seen[chunk_id] = result

    return list(seen.values())


# ---------------------------------------------------------------------------
# Cosine Similarity Helper
# ---------------------------------------------------------------------------
def _cosine_similarity(vec_a: list, vec_b: list) -> float:
    """Compute cosine similarity between two vectors."""
    try:
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def hybrid_search(
    query: str,
    user=None,
    entity=None,
    category_filter: Optional[str] = None,
    top_k: int = DEFAULT_TOP_K,
) -> list:
    """
    Execute the full hybrid retrieval pipeline.

    Args:
        query:           The search query (user's question or compliance check topic).
        user:            Optional User instance for personalised lesson retrieval.
        entity:          Optional Entity instance for entity-specific lesson retrieval.
        category_filter: Optional KnowledgeDocument category to restrict search.
        top_k:           Number of results to return after reranking.

    Returns:
        List of dicts with keys:
            chunk_id, text, document_title, document_id, category,
            source, raw_score, final_score
    """
    from core.eva_service import get_embedding

    # Get query embedding
    try:
        query_embedding = get_embedding(query)
    except Exception as e:
        logger.error(f"Failed to get query embedding: {e}")
        query_embedding = None

    # Stage 1: Vector search
    vector_results = []
    if query_embedding:
        try:
            vector_results = _vector_search(query_embedding, VECTOR_CANDIDATES, category_filter)
        except Exception as e:
            logger.warning(f"Vector search failed: {e}")

    # Stage 2: Keyword search
    keyword_results = []
    try:
        keyword_results = _keyword_search(query, KEYWORD_CANDIDATES, category_filter)
    except Exception as e:
        logger.warning(f"Keyword search failed: {e}")

    # Stage 3: Lesson injection
    lesson_results = []
    if query_embedding:
        try:
            lesson_results = _lesson_search(query_embedding, user=user, entity=entity)
        except Exception as e:
            logger.warning(f"Lesson search failed: {e}")

    # Merge all candidates
    all_candidates = _merge_candidates(vector_results, keyword_results, lesson_results)

    if not all_candidates:
        logger.info("Hybrid search returned no candidates for query: %s", query[:80])
        return []

    # Stage 4: Rerank
    try:
        final_results = _rerank(query, all_candidates, top_k)
    except Exception as e:
        logger.warning(f"Reranker failed: {e}")
        all_candidates.sort(key=lambda x: x["final_score"], reverse=True)
        final_results = all_candidates[:top_k]

    logger.debug(
        "Hybrid search: vector=%d, keyword=%d, lessons=%d, merged=%d, final=%d",
        len(vector_results), len(keyword_results), len(lesson_results),
        len(all_candidates), len(final_results),
    )

    return final_results


def add_fts_index_migration_hint():
    """
    Returns the raw SQL needed to add a tsvector column and GIN index
    to core_knowledgechunk for full text search.

    Run this as a RunSQL migration or execute manually on the server:

        ALTER TABLE core_knowledgechunk
            ADD COLUMN text_search_vector tsvector
            GENERATED ALWAYS AS (to_tsvector('english', coalesce(text, ''))) STORED;

        CREATE INDEX core_knowledgechunk_fts_idx
            ON core_knowledgechunk USING GIN (text_search_vector);
    """
    return {
        "add_column": """
            ALTER TABLE core_knowledgechunk
                ADD COLUMN IF NOT EXISTS text_search_vector tsvector
                GENERATED ALWAYS AS (to_tsvector('english', coalesce(text, ''))) STORED;
        """,
        "add_index": """
            CREATE INDEX IF NOT EXISTS core_knowledgechunk_fts_idx
                ON core_knowledgechunk USING GIN (text_search_vector);
        """,
    }
