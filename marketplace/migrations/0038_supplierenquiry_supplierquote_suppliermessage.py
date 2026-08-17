import decimal
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

from marketplace.constants import TOWN_CHOICES


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0037_user_role_add_supplier'),
    ]

    operations = [
        migrations.CreateModel(
            name='SupplierEnquiry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200)),
                ('description', models.TextField()),
                ('town', models.CharField(max_length=50, choices=TOWN_CHOICES)),
                ('status', models.CharField(
                    choices=[('open', 'Open'), ('quoted', 'Quoted'), ('accepted', 'Accepted'), ('closed', 'Closed')],
                    db_index=True, default='open', max_length=10,
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('client', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='supplier_enquiries',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('supplier', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='received_enquiries',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Supplier Enquiry',
                'verbose_name_plural': 'Supplier Enquiries',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='SupplierQuote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('items', models.JSONField(default=list)),
                ('vep_subtotal', models.DecimalField(decimal_places=2, max_digits=10)),
                ('vat_rate', models.DecimalField(decimal_places=2, default=decimal.Decimal('9.00'), max_digits=5)),
                ('vat_amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('platform_fee_rate', models.DecimalField(decimal_places=2, default=decimal.Decimal('3.00'), max_digits=5)),
                ('platform_fee_amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('total', models.DecimalField(decimal_places=2, max_digits=10)),
                ('message', models.TextField(blank=True)),
                ('lead_time', models.CharField(blank=True, max_length=100)),
                ('valid_until', models.DateField(blank=True, null=True)),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('accepted', 'Accepted'),
                        ('modification_requested', 'Modification Requested'),
                        ('rejected', 'Rejected'),
                    ],
                    db_index=True, default='pending', max_length=25,
                )),
                ('modification_note', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('enquiry', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='quotes',
                    to='marketplace.supplierenquiry',
                )),
                ('supplier', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='supplier_quotes',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Supplier Quote',
                'verbose_name_plural': 'Supplier Quotes',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='SupplierMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('body', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('enquiry', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='messages',
                    to='marketplace.supplierenquiry',
                )),
                ('recipient', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='received_supplier_messages',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('sender', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='sent_supplier_messages',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Supplier Message',
                'verbose_name_plural': 'Supplier Messages',
                'ordering': ['created_at'],
            },
        ),
    ]
