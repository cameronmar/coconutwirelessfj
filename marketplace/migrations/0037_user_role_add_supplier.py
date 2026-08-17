from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0036_supplierprofile'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                blank=True, default='', max_length=10,
                choices=[
                    ('client', 'Client'),
                    ('tradie', 'Tradie'),
                    ('supplier', 'Supplier'),
                    ('', 'Staff / Admin'),
                ],
            ),
        ),
    ]
