"""
Seed additional job/trade categories: Civil Works, Concreting/Cement,
Heavy Equipment.
"""
from django.db import migrations


TRADE_SEED = [
    ('civil_works',      '⛏️', 'Civil Works'),
    ('concreting',       '🪨', 'Concreting / Cement'),
    ('heavy_equipment',  '🚜', 'Heavy Equipment'),
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
        ('marketplace', '0028_publicreview_admin_note_alter_publicreview_task'),
    ]

    operations = [
        migrations.RunPython(seed_categories, reverse_categories),
    ]
