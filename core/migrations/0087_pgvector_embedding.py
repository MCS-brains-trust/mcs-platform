"""
Add pgvector extension and VectorField to KnowledgeChunk.

This migration:
1. Enables the pgvector PostgreSQL extension.
2. Adds an embedding_vector VectorField (1536 dims) to KnowledgeChunk.
3. Back-fills embedding_vector from the existing JSON embedding field.
4. Creates an HNSW index for fast cosine-distance queries.
"""

from django.db import migrations
import pgvector.django


def backfill_embedding_vectors(apps, schema_editor):
    """Copy existing JSON embeddings into the new VectorField column."""
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


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0086_basperiodcommentary_task_tracking"),
    ]

    operations = [
        # 1. Enable the pgvector extension
        pgvector.django.VectorExtension(),

        # 2. Add the VectorField column
        migrations.AddField(
            model_name="knowledgechunk",
            name="embedding_vector",
            field=pgvector.django.VectorField(
                dimensions=1536,
                null=True,
                blank=True,
                help_text="pgvector embedding for native cosine distance search",
            ),
        ),

        # 3. Back-fill from JSON → vector
        migrations.RunPython(
            backfill_embedding_vectors,
            reverse_code=migrations.RunPython.noop,
        ),

        # 4. HNSW index for cosine distance queries
        migrations.AddIndex(
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
