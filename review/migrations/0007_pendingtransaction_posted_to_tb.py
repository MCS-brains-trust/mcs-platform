from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('review', '0006_enhanced_review_workflow'),
    ]

    operations = [
        migrations.AddField(
            model_name='pendingtransaction',
            name='posted_to_tb',
            field=models.BooleanField(
                default=False,
                help_text='True if this transaction has been pushed to the trial balance',
            ),
        ),
    ]
