import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0099_merge_20260319_1205'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='YearEndCommentary',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('section_snapshot', models.TextField(blank=True, default='')),
                ('section_revenue', models.TextField(blank=True, default='')),
                ('section_costs', models.TextField(blank=True, default='')),
                ('section_watch_items', models.TextField(blank=True, default='')),
                ('section_actions', models.TextField(blank=True, default='')),
                ('full_content', models.TextField(blank=True, default='')),
                ('status', models.CharField(
                    choices=[
                        ('generating', 'Generating'),
                        ('draft', 'Draft'),
                        ('reviewed', 'Reviewed'),
                        ('sent', 'Sent to Client'),
                        ('error', 'Error'),
                    ],
                    default='generating',
                    max_length=12,
                )),
                ('tone', models.CharField(
                    choices=[
                        ('professional', 'Professional'),
                        ('conversational', 'Conversational'),
                        ('technical', 'Technical'),
                    ],
                    default='professional',
                    max_length=15,
                )),
                ('version', models.PositiveIntegerField(default=1)),
                ('model_used', models.CharField(blank=True, default='', max_length=20)),
                ('generated_at', models.DateTimeField(auto_now_add=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('context_snapshot', models.JSONField(blank=True, default=dict)),
                ('error_message', models.TextField(blank=True, default='')),
                ('generation_started_at', models.DateTimeField(blank=True, null=True)),
                ('generation_completed_at', models.DateTimeField(blank=True, null=True)),
                ('generation_step', models.CharField(blank=True, default='', max_length=100)),
                ('financial_year', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='year_end_commentary',
                    to='core.financialyear',
                )),
                ('generated_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='generated_yearend_commentaries',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('reviewed_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='reviewed_yearend_commentaries',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Year-End Commentary',
                'verbose_name_plural': 'Year-End Commentaries',
                'ordering': ['-generated_at'],
            },
        ),
    ]
