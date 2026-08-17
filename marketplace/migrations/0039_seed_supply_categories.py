from django.db import migrations

SUPPLY_CATEGORIES = [
    ('building-materials',  '🧱', 'Building Materials',        1),
    ('electrical-supplies', '⚡', 'Electrical Supplies',       2),
    ('plumbing-supplies',   '🔧', 'Plumbing Supplies',         3),
    ('timber-hardware',     '🪵', 'Timber & Hardware',         4),
    ('food-beverage',       '🍱', 'Food & Beverage',           5),
    ('agricultural',        '🌾', 'Agricultural Supplies',     6),
    ('fishing-marine',      '🐟', 'Fishing & Marine Supplies', 7),
    ('office-stationery',   '📎', 'Office & Stationery',       8),
    ('cleaning-hygiene',    '🧹', 'Cleaning & Hygiene',        9),
    ('safety-ppe',          '🦺', 'Safety & PPE',              10),
    ('paint-coatings',      '🎨', 'Paint & Coatings',          11),
    ('tools-equipment',     '🛠️', 'Tools & Equipment',         12),
]


def seed(apps, schema_editor):
    SupplyCategory = apps.get_model('marketplace', 'SupplyCategory')
    for slug, icon, name, order in SUPPLY_CATEGORIES:
        SupplyCategory.objects.get_or_create(slug=slug, defaults={'name': name, 'icon': icon, 'sort_order': order})


def unseed(apps, schema_editor):
    SupplyCategory = apps.get_model('marketplace', 'SupplyCategory')
    SupplyCategory.objects.filter(slug__in=[s[0] for s in SUPPLY_CATEGORIES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0038_supplierenquiry_supplierquote_suppliermessage'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
