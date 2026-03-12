from __future__ import annotations

from django.apps import apps as django_apps
from django.core.management.base import BaseCommand
from secured_fields import mixins as secured_mixins


class Command(BaseCommand):
    help = (
        "Re-encrypt all secured_fields values using the current SECURED_FIELDS_KEY order. "
        "Ensure the NEW key is listed first so encryption uses it."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--app",
            action="append",
            dest="apps",
            help="Limit to app label(s). Can be provided multiple times.",
        )
        parser.add_argument(
            "--model",
            action="append",
            dest="models",
            help=(
                "Limit to model name(s). Accepts ModelName, modelname, "
                "or app_label.modelname. Can be provided multiple times."
            ),
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=200,
            help="Number of rows to process per batch.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List what would be processed without writing to the database.",
        )

    def handle(self, *args, **options):
        app_filters = {label.lower() for label in options.get("apps") or []}
        model_filters = {name.lower() for name in options.get("models") or []}
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]

        def model_allowed(model) -> bool:
            if app_filters and model._meta.app_label.lower() not in app_filters:
                return False
            if not model_filters:
                return True
            return (
                model._meta.model_name.lower() in model_filters
                or model._meta.label_lower in model_filters
                or model._meta.object_name.lower() in model_filters
            )

        total_rows = 0
        processed_models = 0

        for model in django_apps.get_models():
            if not model_allowed(model):
                continue
            if not model._meta.managed:
                continue

            encrypted_fields = [
                field
                for field in model._meta.get_fields()
                if getattr(field, "concrete", False)
                and not getattr(field, "many_to_many", False)
                and isinstance(field, secured_mixins.EncryptedMixin)
            ]

            if not encrypted_fields:
                continue

            field_names = [field.name for field in encrypted_fields]
            qs = model.objects.all().order_by(model._meta.pk.name)
            count = qs.count()
            if count == 0:
                continue

            processed_models += 1
            self.stdout.write(
                f"{model._meta.label}: {count} rows, fields={', '.join(field_names)}"
            )

            if dry_run:
                continue

            batch = []
            for obj in qs.iterator(chunk_size=batch_size):
                batch.append(obj)
                if len(batch) >= batch_size:
                    model.objects.bulk_update(batch, field_names, batch_size=batch_size)
                    total_rows += len(batch)
                    batch = []

            if batch:
                model.objects.bulk_update(batch, field_names, batch_size=batch_size)
                total_rows += len(batch)

        if dry_run:
            self.stdout.write(
                self.style.WARNING("Dry run complete. No data was written.")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Re-encryption complete. Models: {processed_models}, Rows updated: {total_rows}."
                )
            )
