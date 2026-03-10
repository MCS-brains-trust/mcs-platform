from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0065_workpaper_template'),
    ]

    operations = [
        migrations.AddField(
            model_name='depreciationasset',
            name='notes',
            field=models.TextField(
                blank=True,
                default='',
                help_text='Internal notes, e.g. roll-forward provenance',
            ),
        ),
    ]
