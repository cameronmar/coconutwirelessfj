from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0033_seed_massage'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='fcm_token',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
