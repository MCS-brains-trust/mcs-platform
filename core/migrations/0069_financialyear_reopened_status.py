from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0068_trialbalanceline_source_journal'),
    ]

    operations = [
        migrations.AlterField(
            model_name='financialyear',
            name='status',
            field=models.CharField(
                choices=[
                    ('draft', 'Draft'),
                    ('in_review', 'In Review'),
                    ('finished', 'Finished'),
                    ('prepared', 'Prepared (Eva Reviewing)'),
                    ('pending_eva', 'Pending Eva Review'),
                    ('eva_cleared', 'Eva Cleared'),
                    ('eva_error', 'Eva Error'),
                    ('locked', 'Locked'),
                    ('finalised', 'Finalised'),
                    ('reopened', 'Reopened'),
                ],
                default='draft',
                max_length=20,
            ),
        ),
    ]
