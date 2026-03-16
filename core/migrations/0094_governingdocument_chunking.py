from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0093_update_financial_statement_template"),
    ]

    operations = [
        migrations.AddField(
            model_name="governingdocument",
            name="chunk_count",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Number of retrieval chunks indexed from the extracted governing document text",
            ),
        ),
        migrations.CreateModel(
            name="GoverningDocumentChunk",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("chunk_index", models.IntegerField(help_text="Position of this chunk within the governing document")),
                ("heading", models.CharField(max_length=255, blank=True, default="")),
                ("text", models.TextField(help_text="Chunk text used for governing-document retrieval")),
                ("start_char", models.IntegerField(default=0)),
                ("end_char", models.IntegerField(default=0)),
                ("token_count", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "entity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="governing_document_chunks",
                        to="core.entity",
                    ),
                ),
                (
                    "governing_document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chunks",
                        to="core.governingdocument",
                    ),
                ),
            ],
            options={
                "ordering": ["governing_document", "chunk_index"],
                "unique_together": {("governing_document", "chunk_index")},
            },
        ),
        migrations.AddIndex(
            model_name="governingdocumentchunk",
            index=models.Index(fields=["entity", "governing_document"], name="core_govdocc_entity__7f0b6f_idx"),
        ),
        migrations.AddIndex(
            model_name="governingdocumentchunk",
            index=models.Index(fields=["governing_document", "chunk_index"], name="core_govdocc_govdoc__1c5356_idx"),
        ),
    ]
