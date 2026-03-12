from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0012_historicalclinic_slug_nonnull'),
    ]

    operations = [
        migrations.CreateModel(
            name='AdminBranding',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('site_header', models.CharField(default='ClinicOps Admin', max_length=100)),
                ('site_title', models.CharField(default='ClinicOps Admin', max_length=100)),
                ('index_title', models.CharField(default='ClinicOps Administration', max_length=200)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Admin Branding',
                'verbose_name_plural': 'Admin Branding',
            },
        ),
    ]
