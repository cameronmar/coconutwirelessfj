from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0039_seed_supply_categories'),
    ]

    operations = [
        migrations.AddField(
            model_name='message',
            name='delivered_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='message',
            name='read_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='suppliermessage',
            name='delivered_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='suppliermessage',
            name='read_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
