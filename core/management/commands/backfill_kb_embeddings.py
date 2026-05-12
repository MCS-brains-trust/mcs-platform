"""
Management command: backfill_kb_embeddings

Populates KnowledgeChunk.embedding_vector (pgvector) for rows that have a
JSON `embedding` but no `embedding_vector`. This fixes the dual-write defect
where the legacy SharePoint sync wrote only the JSON field, leaving chunks
excluded from RAG retrieval.

Reuses core.eva_knowledge._get_embeddings (the batched, dimension-pinned
OpenAI helper) so we don't duplicate client setup or risk dimension drift.

Idempotent: re-running after a successful pass is a no-op (zero NULLs found,
zero processed, zero errors).

Usage:
    python3 manage.py backfill_kb_embeddings
    python3 manage.py backfill_kb_embeddings --dry-run
    python3 manage.py backfill_kb_embeddings --limit 100
"""
import logging

from django.core.management.base import BaseCommand

from core.eva_knowledge import _get_embeddings
from core.models import KnowledgeChunk

logger = logging.getLogger(__name__)

BATCH_SIZE = 50


class Command(BaseCommand):
    help = (
        "Backfill KnowledgeChunk.embedding_vector (pgvector) for rows where it "
        "is NULL. Idempotent — re-running after success is a no-op."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Report how many chunks would be backfilled without calling OpenAI or writing.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Maximum number of chunks to process this run (0 = no limit).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]

        qs = (
            KnowledgeChunk.objects
            .filter(embedding_vector__isnull=True)
            .order_by("id")
        )
        total_null = qs.count()

        if dry_run:
            target = min(total_null, limit) if limit > 0 else total_null
            self.stdout.write(f"Would backfill {target} chunks.")
            return

        if total_null == 0:
            self.stdout.write("Backfilled 0 chunks. Remaining NULL: 0. Errors: 0.")
            return

        target = min(total_null, limit) if limit > 0 else total_null
        # Materialise the slice once so subsequent batches are stable even if
        # other writers are touching the table.
        chunks = list(qs[:target].only("id", "text", "embedding_vector"))

        written = 0
        errors = 0
        processed = 0

        for i in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[i:i + BATCH_SIZE]

            # Filter out empty-text chunks before calling OpenAI — the API
            # rejects empty strings and we don't want garbage embeddings.
            usable = []
            for c in batch:
                if not (c.text and c.text.strip()):
                    logger.warning("Skipping chunk %s — empty text", c.id)
                    errors += 1
                    processed += 1
                    continue
                usable.append(c)

            if not usable:
                self._progress(processed, target, written, errors)
                continue

            texts = [c.text for c in usable]
            try:
                embeddings = _get_embeddings(texts)
            except Exception as e:
                # _get_embeddings already catches and returns [[]] on failure,
                # but we keep this as a defensive boundary against helper
                # changes or unexpected client errors.
                logger.error(
                    "Embedding API call raised for batch starting at chunk %s "
                    "(size %d): %s",
                    usable[0].id, len(usable), e,
                )
                errors += len(usable)
                processed += len(usable)
                self._progress(processed, target, written, errors)
                continue

            if len(embeddings) != len(usable):
                logger.error(
                    "Embedding helper returned %d vectors for %d chunks "
                    "(batch starting at %s) — counting batch as errors, no writes.",
                    len(embeddings), len(usable), usable[0].id,
                )
                errors += len(usable)
                processed += len(usable)
                self._progress(processed, target, written, errors)
                continue

            to_update = []
            for chunk, vector in zip(usable, embeddings):
                if not vector:
                    # Whole-batch failure surfaces as [[], [], ...] from the helper.
                    logger.error(
                        "Empty embedding returned for chunk %s — skipping write",
                        chunk.id,
                    )
                    errors += 1
                    processed += 1
                    continue
                chunk.embedding_vector = vector
                to_update.append(chunk)
                processed += 1

            if to_update:
                KnowledgeChunk.objects.bulk_update(to_update, ["embedding_vector"])
                written += len(to_update)

            self._progress(processed, target, written, errors)

        # Re-query NULL count from the live table — the slice we materialised
        # may not reflect concurrent writes (and target itself was a snapshot).
        remaining = KnowledgeChunk.objects.filter(embedding_vector__isnull=True).count()
        self.stdout.write(
            f"Backfilled {written} chunks. Remaining NULL: {remaining}. Errors: {errors}."
        )

    def _progress(self, processed, target, written, errors):
        if target <= 0:
            return
        pct = int((processed / target) * 100)
        self.stdout.write(
            f"Backfilled {processed}/{target} ({pct}%) — "
            f"{written} written, {errors} errors so far"
        )
