from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('review', '0007_merge_0006_0007_alter'),
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
