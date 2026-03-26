from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import Plan
from core.paypal import PayPalError, create_plan, create_product


class Command(BaseCommand):
    help = 'Create PayPal product/plan IDs for paid Plan records without paypal_plan_id.'

    def add_arguments(self, parser):
        parser.add_argument('--product-name', default='ClinicOps Subscription', help='PayPal product name.')
        parser.add_argument('--product-description', default='ClinicOps SaaS subscription', help='Product description.')
        parser.add_argument('--product-id', help='Existing PayPal product ID to use.')
        parser.add_argument('--force', action='store_true', help='Recreate plan IDs even if they exist.')

    def handle(self, *args, **options):
        product_id = options['product_id'] or settings.PAYPAL_PRODUCT_ID
        if not product_id:
            self.stdout.write('Creating PayPal product...')
            try:
                product_id = create_product(
                    options['product_name'],
                    options['product_description'],
                )
            except PayPalError as exc:
                self.stderr.write(str(exc))
                return
            self.stdout.write(self.style.SUCCESS(f'Created product: {product_id}'))
            self.stdout.write('Set PAYPAL_PRODUCT_ID in your .env to reuse this product.')

        plans = Plan.objects.filter(is_active=True).order_by('price_cents')
        if not plans.exists():
            self.stdout.write('No plans found. Create plans in admin first.')
            return

        for plan in plans:
            if plan.is_free:
                self.stdout.write(f'Skipping {plan.name} (free plans do not sync to PayPal).')
                continue
            if plan.paypal_plan_id and not options['force']:
                self.stdout.write(f'Skipping {plan.name} (already has PayPal plan id).')
                continue

            interval_unit = 'MONTH' if plan.interval == Plan.Interval.MONTH else 'YEAR'
            price_value = f'{plan.price_cents / 100:.2f}'
            try:
                paypal_plan_id = create_plan(
                    product_id=product_id,
                    name=plan.name,
                    interval_unit=interval_unit,
                    price_value=price_value,
                    currency_code=plan.currency,
                )
            except PayPalError as exc:
                self.stderr.write(f'Failed to create plan for {plan.name}: {exc}')
                continue

            plan.paypal_plan_id = paypal_plan_id
            plan.save(update_fields=['paypal_plan_id'])
            self.stdout.write(self.style.SUCCESS(f'{plan.name}: {paypal_plan_id}'))
