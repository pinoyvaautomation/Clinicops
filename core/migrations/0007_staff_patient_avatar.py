from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_patient_user'),
    ]

    operations = [
        migrations.AddField(
            model_name='staff',
            name='avatar',
            field=models.ImageField(blank=True, null=True, upload_to='avatars/staff/'),
        ),
        migrations.AddField(
            model_name='patient',
            name='avatar',
            field=models.ImageField(blank=True, null=True, upload_to='avatars/patients/'),
        ),
    ]
