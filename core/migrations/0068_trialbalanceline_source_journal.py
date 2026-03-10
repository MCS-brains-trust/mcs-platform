from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0067_merge_0066'),
    ]

    operations = [
        migrations.AddField(
            model_name='trialbalanceline',
            name='source_journal',
            field=models.ForeignKey(
                blank=True,
                help_text='The manual journal that created this adjustment TB line',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='tb_lines',
                to='core.adjustingjournal',
            ),
        ),
    ]
