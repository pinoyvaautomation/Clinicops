from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_migrate
from django.dispatch import receiver
from django.db import connection
from django.utils import timezone

from django_q.models import Schedule

GROUPS = ['Admin', 'Doctor', 'Nurse', 'FrontDesk', 'Patient']


@receiver(post_migrate)
def ensure_groups_and_schedules(**kwargs):
    app_config = kwargs.get('app_config') or kwargs.get('sender')
    if getattr(app_config, 'label', None) != 'core':
        return
    groups = {}
    for name in GROUPS:
        group, _ = Group.objects.get_or_create(name=name)
        groups[name] = group

    model_perms = {
        'Admin': {
            'clinic': ['add', 'change', 'delete', 'view'],
            'staff': ['add', 'change', 'delete', 'view'],
            'patient': ['add', 'change', 'delete', 'view'],
            'appointment': ['add', 'change', 'delete', 'view'],
            'appointmenttype': ['add', 'change', 'delete', 'view'],
            'plan': ['add', 'change', 'delete', 'view'],
            'clinicsubscription': ['add', 'change', 'delete', 'view'],
        },
        'Doctor': {
            'patient': ['view', 'change'],
            'appointment': ['view', 'change'],
        },
        'Nurse': {
            'patient': ['view'],
            'appointment': ['view', 'change'],
        },
        'FrontDesk': {
            'patient': ['view'],
            'appointment': ['view', 'change'],
        },
    }

    for group_name, perms_map in model_perms.items():
        group = groups[group_name]
        for model_label, actions in perms_map.items():
            content_type = ContentType.objects.get(app_label='core', model=model_label)
            for action in actions:
                codename = f'{action}_{model_label}'
                perm = Permission.objects.filter(content_type=content_type, codename=codename).first()
                if perm and not group.permissions.filter(id=perm.id).exists():
                    group.permissions.add(perm)

    try:
        tables = connection.introspection.table_names()
    except Exception:
        return

    if Schedule._meta.db_table not in tables:
        return

    Schedule.objects.get_or_create(
        name='appointment-reminders',
        defaults={
            'func': 'core.tasks.send_upcoming_appointment_reminders',
            'schedule_type': Schedule.MINUTES,
            'minutes': 10,
            'repeats': -1,
            'next_run': timezone.now(),
        },
    )
