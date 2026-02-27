"""
Migration: Eva v2 — Knowledge Brain + Chat Interface
- Creates KnowledgeDocument model
- Creates KnowledgeChunk model
- Creates EvaConversation model
- Creates EvaMessage model
- Adds opus_override to EvaReview
- Adds title and knowledge_brain_citation to EvaFinding
"""
import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0044_eva_ai_compliance_reviewer"),
    ]

    operations = [
        # ── EvaReview: add opus_override ──────────────────────────────
        migrations.AddField(
            model_name="evareview",
            name="opus_override",
            field=models.BooleanField(
                default=False,
                help_text="True if manually escalated to Opus model",
            ),
        ),
        # ── EvaFinding: add title and knowledge_brain_citation ────────
        migrations.AddField(
            model_name="evafinding",
            name="title",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Brief finding title e.g. 'Potential Division 7A Exposure'",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="evafinding",
            name="knowledge_brain_citation",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Knowledge Brain document cited, if applicable",
                max_length=500,
            ),
        ),
        # ── KnowledgeDocument ─────────────────────────────────────────
        migrations.CreateModel(
            name="KnowledgeDocument",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=500)),
                ("category", models.CharField(
                    choices=[
                        ("firm_procedures", "Firm Procedures"),
                        ("firm_technical", "Firm Technical Positions"),
                        ("firm_training", "Firm Training"),
                        ("firm_precedents", "Firm Precedents"),
                        ("ato_rulings", "ATO Rulings"),
                        ("ato_statements", "ATO Practice Statements"),
                        ("ato_alerts", "ATO Alerts"),
                        ("ato_benchmarks", "ATO Benchmarks"),
                        ("legislation", "Legislation"),
                        ("aasb_standards", "AASB Standards"),
                        ("cpa_materials", "CPA Materials"),
                        ("ca_anz_materials", "CA ANZ Materials"),
                        ("treasury", "Treasury"),
                    ],
                    default="firm_procedures",
                    max_length=30,
                )),
                ("sharepoint_path", models.CharField(blank=True, default="", help_text="Full SharePoint path to the source document", max_length=1000)),
                ("sharepoint_item_id", models.CharField(blank=True, default="", help_text="SharePoint item ID for API operations", max_length=255)),
                ("sharepoint_modified_at", models.DateTimeField(blank=True, null=True)),
                ("sync_status", models.CharField(
                    choices=[("pending", "Pending"), ("synced", "Synced"), ("error", "Error")],
                    default="pending",
                    max_length=10,
                )),
                ("synced_at", models.DateTimeField(blank=True, null=True)),
                ("chunk_count", models.IntegerField(default=0)),
                ("file_type", models.CharField(blank=True, default="", help_text="File extension: docx, pdf, txt, xlsx, pptx", max_length=10)),
                ("file_size_bytes", models.IntegerField(default=0)),
                ("is_archived", models.BooleanField(default=False, help_text="Archived documents are excluded from Eva's active retrieval")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-updated_at"],
                "indexes": [
                    models.Index(fields=["category", "sync_status"], name="core_knowdoc_cat_sync_idx"),
                    models.Index(fields=["sharepoint_item_id"], name="core_knowdoc_sp_item_idx"),
                ],
            },
        ),
        # ── KnowledgeChunk ────────────────────────────────────────────
        migrations.CreateModel(
            name="KnowledgeChunk",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("chunk_index", models.IntegerField(help_text="Position of this chunk within the document")),
                ("text", models.TextField(help_text="Raw text of this chunk (~512 tokens)")),
                ("embedding", models.JSONField(blank=True, default=list, help_text="Vector embedding (1536 dimensions) as JSON array")),
                ("token_count", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("document", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="chunks",
                    to="core.knowledgedocument",
                )),
            ],
            options={
                "ordering": ["document", "chunk_index"],
                "unique_together": {("document", "chunk_index")},
                "indexes": [
                    models.Index(fields=["document", "chunk_index"], name="core_knowchunk_doc_idx"),
                ],
            },
        ),
        # ── EvaConversation ───────────────────────────────────────────
        migrations.CreateModel(
            name="EvaConversation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_active_at", models.DateTimeField(auto_now=True)),
                ("message_count", models.IntegerField(default=0)),
                ("financial_year", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="eva_conversations",
                    to="core.financialyear",
                )),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="eva_conversations",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ["-last_active_at"],
                "indexes": [
                    models.Index(fields=["financial_year", "user"], name="core_evaconvo_fy_user_idx"),
                ],
            },
        ),
        # ── EvaMessage ────────────────────────────────────────────────
        migrations.CreateModel(
            name="EvaMessage",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("role", models.CharField(choices=[("user", "User"), ("assistant", "Assistant")], max_length=10)),
                ("content", models.TextField()),
                ("model_used", models.CharField(blank=True, default="", help_text="AI model used: haiku/sonnet/opus (blank for user messages)", max_length=10)),
                ("retrieved_chunk_ids", models.JSONField(blank=True, default=list, help_text="List of KnowledgeChunk IDs used in this response")),
                ("tokens_used", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("conversation", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="messages",
                    to="core.evaconversation",
                )),
            ],
            options={
                "ordering": ["created_at"],
                "indexes": [
                    models.Index(fields=["conversation", "created_at"], name="core_evamsg_convo_dt_idx"),
                ],
            },
        ),
    ]
