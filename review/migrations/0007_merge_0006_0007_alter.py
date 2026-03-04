from django.db import migrations


class Migration(migrations.Migration):
    """
    Merge migration to resolve conflict between:
    - 0006_enhanced_review_workflow (in repo)
    - 0007_alter_entitygstsetting_financial_year (created on server)
    """

    dependencies = [
        ('review', '0006_enhanced_review_workflow'),
        ('review', '0007_alter_entitygstsetting_financial_year'),
    ]

    operations = [
    ]
