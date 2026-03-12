from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0013_admin_branding"),
    ]

    operations = [
        migrations.AlterField(
            model_name="patient",
            name="user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="patient_profiles",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
