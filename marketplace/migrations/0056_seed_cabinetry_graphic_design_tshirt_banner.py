"""
Seed additional job/trade categories: Cabinetry, Graphic Design,
T-Shirt/Banner/Flags.
"""
from django.db import migrations


TRADE_SEED = [
    ('cabinetry',          '🗄️', 'Cabinetry'),
    ('graphic_design',     '🎨', 'Graphic Design'),
    ('tshirt_banner_flags', '👕', 'T-Shirt/Banner/Flags'),
]


def seed_categories(apps, schema_editor):
    TradeCategory = apps.get_model('marketplace', 'TradeCategory')
    for slug, icon, name in TRADE_SEED:
        TradeCategory.objects.get_or_create(
            slug=slug,
            defaults={'name': name, 'icon': icon, 'active': True},
        )


def reverse_categories(apps, schema_editor):
    TradeCategory = apps.get_model('marketplace', 'TradeCategory')
    TradeCategory.objects.filter(slug__in=[slug for slug, _, _ in TRADE_SEED]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0055_socialaccount_socialaccount_uniq_provider_uid_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_categories, reverse_categories),
    ]
