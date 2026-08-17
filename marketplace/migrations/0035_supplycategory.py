from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0034_user_fcm_token'),
    ]

    operations = [
        migrations.CreateModel(
            name='SupplyCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=50, unique=True)),
                ('icon', models.CharField(blank=True, max_length=10)),
                ('slug', models.SlugField(unique=True)),
                ('active', models.BooleanField(default=True)),
                ('sort_order', models.PositiveIntegerField(default=0)),
            ],
            options={
                'verbose_name': 'Supply Category',
                'verbose_name_plural': 'Supply Categories',
                'ordering': ['sort_order', 'name'],
            },
        ),
    ]
