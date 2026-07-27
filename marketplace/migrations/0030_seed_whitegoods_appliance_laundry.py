"""
Seed additional job/trade categories: Whitegoods, Small Appliance Repair,
Laundry Tech.
"""
from django.db import migrations


TRADE_SEED = [
    ('whitegoods',       '🧊', 'Whitegoods'),
    ('small_appliance',  '🔌', 'Small Appliance Repair'),
    ('laundry_tech',     '🧺', 'Laundry Tech'),
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
        ('marketplace', '0029_seed_civil_works'),
    ]

    operations = [
        migrations.RunPython(seed_categories, reverse_categories),
    ]
