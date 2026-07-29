"""
Seed additional job/trade category: Massage.
"""
from django.db import migrations


TRADE_SEED = [
    ('massage', '💆', 'Massage'),
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
        ('marketplace', '0032_platformsettings_market_fee_rate'),
    ]

    operations = [
        migrations.RunPython(seed_categories, reverse_categories),
    ]
