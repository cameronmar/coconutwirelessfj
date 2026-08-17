import decimal
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0035_supplycategory'),
    ]

    operations = [
        migrations.CreateModel(
            name='SupplierProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('business_name', models.CharField(blank=True, max_length=100)),
                ('tin', models.CharField(blank=True, max_length=50, verbose_name='TIN Number (optional)')),
                ('bio', models.TextField(blank=True)),
                ('supply_categories', models.JSONField(default=list)),
                ('service_towns', models.JSONField(default=list)),
                ('tin_letter', models.FileField(blank=True, null=True, upload_to='supplier_documents/')),
                ('business_registration', models.FileField(blank=True, null=True, upload_to='supplier_documents/')),
                ('import_export_licence', models.FileField(blank=True, null=True, upload_to='supplier_documents/')),
                ('verification_status', models.CharField(
                    choices=[('pending', 'Pending review'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('suspended', 'Suspended')],
                    db_index=True, default='pending', max_length=20,
                )),
                ('documents_verified', models.BooleanField(default=False)),
                ('verification_notes', models.TextField(blank=True)),
                ('is_founding_member', models.BooleanField(default=False)),
                ('founding_member_credit_balance', models.DecimalField(decimal_places=2, default=decimal.Decimal('0.00'), max_digits=10)),
                ('user', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='supplier_profile',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Supplier Profile',
                'verbose_name_plural': 'Supplier Profiles',
            },
        ),
    ]
