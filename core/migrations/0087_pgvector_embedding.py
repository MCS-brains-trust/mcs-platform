"""
Add pgvector extension and VectorField to KnowledgeChunk.

This migration:
1. Enables the pgvector PostgreSQL extension.
2. Adds an embedding_vector VectorField (1536 dims) to KnowledgeChunk.
3. Back-fills embedding_vector from the existing JSON embedding field.
4. Creates an HNSW index for fast cosine-distance queries.

NOTE: Operations 1, 3, and 4 are PostgreSQL-only and are skipped on
other backends (e.g. SQLite used in tests). Operation 2 (AddField) is
handled via a RunSQL/RunPython that degrades gracefully.
"""

from django.db import migrations, connection
import pgvector.django


def _is_postgres(schema_editor):
    return schema_editor.connection.vendor == "postgresql"


def backfill_embedding_vectors(apps, schema_editor):
    """Copy existing JSON embeddings into the new VectorField column."""
    if not _is_postgres(schema_editor):
        return
    KnowledgeChunk = apps.get_model("core", "KnowledgeChunk")
    batch = []
    for chunk in KnowledgeChunk.objects.iterator(chunk_size=500):
        if chunk.embedding and len(chunk.embedding) == 1536:
            chunk.embedding_vector = chunk.embedding
            batch.append(chunk)
        if len(batch) >= 500:
            KnowledgeChunk.objects.bulk_update(batch, ["embedding_vector"])
            batch = []
    if batch:
        KnowledgeChunk.objects.bulk_update(batch, ["embedding_vector"])


class SafeVectorExtension(pgvector.django.VectorExtension):
    """VectorExtension that is a no-op on non-PostgreSQL backends."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if _is_postgres(schema_editor):
            super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if _is_postgres(schema_editor):
            super().database_backwards(app_label, schema_editor, from_state, to_state)


class SafeAddVectorField(migrations.AddField):
    """AddField that is a no-op on non-PostgreSQL backends (VectorField is PG-only)."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if _is_postgres(schema_editor):
            super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if _is_postgres(schema_editor):
            super().database_backwards(app_label, schema_editor, from_state, to_state)


class SafeAddHnswIndex(migrations.AddIndex):
    """AddIndex that is a no-op on non-PostgreSQL backends (HNSW is PG-only)."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if _is_postgres(schema_editor):
            super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if _is_postgres(schema_editor):
            super().database_backwards(app_label, schema_editor, from_state, to_state)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0086_basperiodcommentary_task_tracking"),
    ]

    operations = [
        # 1. Enable the pgvector extension (PostgreSQL only)
        SafeVectorExtension(),

        # 2. Add the VectorField column (PostgreSQL only)
        SafeAddVectorField(
            model_name="knowledgechunk",
            name="embedding_vector",
            field=pgvector.django.VectorField(
                dimensions=1536,
                null=True,
                blank=True,
                help_text="pgvector embedding for native cosine distance search",
            ),
        ),

        # 3. Back-fill from JSON → vector (PostgreSQL only, guarded inside function)
        migrations.RunPython(
            backfill_embedding_vectors,
            reverse_code=migrations.RunPython.noop,
        ),

        # 4. HNSW index for cosine distance queries (PostgreSQL only)
        SafeAddHnswIndex(
            model_name="knowledgechunk",
            index=pgvector.django.HnswIndex(
                name="knowledge_chunk_embedding_hnsw",
                fields=["embedding_vector"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ),
    ]
