# ClinicOps QA Checklist

Use this checklist before staging or production deploys.

## 1. Preflight

- [ ] Pull latest code and install dependencies if needed.
- [ ] Apply migrations.

```powershell
python manage.py migrate
```

- [ ] Confirm the app starts locally.

```powershell
python manage.py runserver
```

- [ ] Confirm you have test data for:
  - [ ] clinic owner account
  - [ ] staff account
  - [ ] patient account
  - [ ] clinic with active subscription
  - [ ] clinic with inactive subscription

## 2. Automated Checks

- [ ] Run unit tests.

```powershell
python manage.py test core
```

- [ ] Run Django checks.

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
```

- [ ] Run deploy checks with production-shaped values.

```powershell
$env:DEBUG='false'
$env:SECRET_KEY='replace-with-a-long-random-secret-key-value'
$env:ALLOWED_HOSTS='example.com'
$env:DJANGO_SECURE='true'
$env:SECURED_FIELDS_KEY='replace-with-real-secured-fields-key'
$env:SECURED_FIELDS_HASH_SALT='replace-with-real-hash-salt'
$env:PAYPAL_VERIFY_WEBHOOK='true'
$env:PAYPAL_WEBHOOK_ID='replace-with-real-paypal-webhook-id'
python manage.py check --deploy
```

## 3. Public Auth Pages

- [ ] Open `/accounts/login/`
  - [ ] layout loads correctly
  - [ ] `Sign up` button in top nav works
  - [ ] invalid credentials show an error

- [ ] Open `/signup/`
  - [ ] signup page uses branded shell
  - [ ] step 1 account form submits
  - [ ] page switches into payment state after clinic creation

- [ ] Open `/accounts/password_reset/`
  - [ ] branded reset page loads
  - [ ] reset request submits without template errors

- [ ] Open password reset email link flow
  - [ ] password reset confirm page loads
  - [ ] valid link updates password
  - [ ] invalid or expired link shows safe error state

- [ ] Open `/resend-verification/`
  - [ ] branded resend verification page loads
  - [ ] resend success state renders correctly

- [ ] Test verify email flow
  - [ ] valid link shows success state
  - [ ] invalid link shows recovery path

- [ ] Visit a missing URL
  - [ ] branded 404 page renders

## 4. Public Booking Flow

- [ ] Open a clinic booking page for an active clinic
  - [ ] appointment type selector works
  - [ ] slot list updates after changing service
  - [ ] form validation errors render correctly

- [ ] Submit a valid booking
  - [ ] booking success page shows clinic, staff, service, time, and confirmation code
  - [ ] appointment lookup link works

- [ ] Open `/appointments/lookup/`
  - [ ] valid email + confirmation code returns appointment details
  - [ ] invalid values show an error message

- [ ] Open a booking page for an inactive clinic subscription
  - [ ] booking blocked page renders
  - [ ] copy tells the patient to contact the clinic directly

## 5. Patient Signup Flow

- [ ] Open clinic patient signup page
  - [ ] branded shell loads
  - [ ] form fields and errors render correctly

- [ ] Submit a new patient signup
  - [ ] success page renders
  - [ ] verification email messaging is correct

- [ ] Submit signup with existing linked patient email
  - [ ] safe validation error is shown

- [ ] Submit signup with existing user not yet linked to clinic
  - [ ] account links successfully

## 6. Clinic Portal Smoke Test

- [ ] Log in as clinic owner
  - [ ] dashboard loads
  - [ ] top nav works on desktop
  - [ ] mobile menu works in responsive mode

- [ ] Staff management
  - [ ] add staff modal opens
  - [ ] edit staff modal opens
  - [ ] create staff saves successfully
  - [ ] edit staff saves successfully
  - [ ] toast notification appears with the right name

- [ ] Services management
  - [ ] add service modal opens
  - [ ] edit service modal opens
  - [ ] modal fits on laptop-sized screen
  - [ ] create service saves successfully
  - [ ] edit service saves successfully
  - [ ] toast notification appears with the right service name

- [ ] Appointments
  - [ ] create appointment modal opens
  - [ ] appointment saves successfully
  - [ ] appointment list updates correctly

- [ ] Patients
  - [ ] patient list loads
  - [ ] patient edit page works

- [ ] Settings and password change
  - [ ] password change page loads with branded layout
  - [ ] password change succeeds

## 7. Billing and Subscription

- [ ] Clinic signup payment flow works in PayPal sandbox
- [ ] Billing page activate flow works
- [ ] Billing sync action works
- [ ] Subscription status updates correctly in portal

- [ ] PayPal webhook handling
  - [ ] valid webhook updates local subscription status
  - [ ] duplicate webhook does not double-process
  - [ ] webhook event is recorded in the database

- [ ] Subscription gating
  - [ ] active subscription allows booking
  - [ ] inactive subscription blocks booking

## 8. Responsive Review

Check at these viewport sizes in browser dev tools:

- [ ] `390 x 844`
- [ ] `768 x 1024`
- [ ] `1024 x 768`

Review these pages at each size:

- [ ] login
- [ ] clinic signup
- [ ] password reset
- [ ] resend verification
- [ ] patient signup
- [ ] booking
- [ ] booking success
- [ ] appointment lookup
- [ ] clinic dashboard
- [ ] staff page
- [ ] services page

Verify:

- [ ] nav is usable
- [ ] cards do not overflow horizontally
- [ ] modals fit in viewport
- [ ] buttons remain visible
- [ ] forms are readable and submit normally

## 9. Regression Watch List

Check specifically for:

- [ ] broken redirects after submit
- [ ] missing CSRF token errors
- [ ] stale success messages or duplicate toasts
- [ ] modal forms not reopening on validation errors
- [ ] PayPal subscription mismatch after webhook events
- [ ] booking lookup not matching confirmation code
- [ ] pages rendering old unbranded layout unexpectedly

## 10. Release Sign-off

- [ ] All automated checks passed
- [ ] Manual smoke test passed
- [ ] Responsive review passed
- [ ] Billing sandbox test passed
- [ ] No blocker bugs found

Release tested by: __________________

Date tested: __________________

Notes:

- ________________________________________
- ________________________________________
- ________________________________________
