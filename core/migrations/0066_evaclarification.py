from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0065_workpaper_template"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EvaClarification",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("question_id", models.CharField(help_text="Identifier of the question from CLARIFICATION_QUESTIONS", max_length=100)),
                ("question_text", models.TextField(help_text="The question as shown to the accountant")),
                ("answer_value", models.CharField(help_text="The selected option value (e.g. 'related_company')", max_length=100)),
                ("answer_label", models.CharField(blank=True, default="", help_text="The human-readable label of the selected option", max_length=255)),
                ("answer_detail", models.TextField(blank=True, default="", help_text="Optional free-text elaboration from the accountant")),
                ("outcome_hint", models.CharField(blank=True, default="", help_text="Outcome hint from the option definition", max_length=20)),
                ("outcome", models.CharField(choices=[("pending", "Pending"), ("dismissed", "Dismissed"), ("confirmed", "Confirmed"), ("reduced", "Severity Reduced")], default="pending", max_length=15)),
                ("outcome_message", models.TextField(blank=True, default="", help_text="Eva's explanation of how this answer affects the finding")),
                ("learning_note", models.TextField(blank=True, default="", help_text="Note stored for future reviews")),
                ("answered_at", models.DateTimeField(auto_now_add=True)),
                ("finding", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="clarifications", to="core.evafinding")),
                ("answered_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="eva_clarifications", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["answered_at"],
            },
        ),
    ]
