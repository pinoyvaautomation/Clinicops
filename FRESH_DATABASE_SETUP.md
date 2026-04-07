# Fresh Database Setup Checklist

Use this checklist when creating a brand new database for ClinicOps on Render or another environment.

## 1. Create The New Database

- Create the new PostgreSQL database
- Copy the new `DATABASE_URL`

## 2. Update Environment Variables

Confirm the app still has these environment variables:

- `DATABASE_URL`
- `SECRET_KEY`
- `ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS`
- `DEFAULT_FROM_EMAIL`
- `EMAIL_BACKEND`
- `RESEND_API_KEY`
- `PAYPAL_CLIENT_ID`
- `PAYPAL_CLIENT_SECRET`
- `PAYPAL_WEBHOOK_ID`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `PLATFORM_ALERT_EMAILS`
- `SECURITY_ALERT_EMAILS`

## 3. Apply Migrations

Run:

```powershell
python manage.py migrate
```

## 4. Create Superuser

Run:

```powershell
python manage.py createsuperuser
```

## 5. Recreate Plans In Admin

Log in to `/admin/` and create:

### Free

- `is_free = true`
- `price_cents = 0`
- no `paypal_plan_id`
- set Free limits

### Premium

- `is_free = false`
- set real `paypal_plan_id`
- set Premium price
- enable Premium features as needed:
  - reminders
  - notifications
  - messaging
  - waitlist
  - branding

## 6. Recreate Promo Codes

If you are using launch promos:

- create the promo in admin
- connect it to the base `Premium` plan
- set the promo PayPal plan ID
- set `max_redemptions`
- set start / end dates if needed

## 7. Create Test Data

Create at least:

- `1 Free test clinic`
- `1 Premium test clinic`
- optional demo clinic for screenshots or demos

## 8. Verify Core Flows

Test:

- clinic signup
- email verification
- password reset
- login
- Google sign-in
- Free activation
- Premium activation
- booking page
- patient signup
- self-service cancel/reschedule
- messaging
- waitlist
- help / feedback form
- platform alerts
- security alerts

## 9. Verify Admin Records

Confirm the admin contains:

- Plans
- Promo codes
- Subscriptions
- Security rules
- Help requests

## 10. Final Launch Reminder

Free database is acceptable for short-term QA and testing, but before public launch you should move to a paid production database with backups enabled.

## 11. Best Practical Order

After migration:

1. Create `Free`
2. Create `Premium`
3. Create founder promo code
4. Create one test clinic
5. Run the QA checklist
