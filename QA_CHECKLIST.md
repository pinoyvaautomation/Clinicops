# ClinicOps Full QA Checklist

Use this checklist before staging demos, release candidates, and production deploys. It is written to validate the current ClinicOps MVP from the UI, role, and flow perspective.

## 1. Test Setup

- [ ] Pull latest code
- [ ] Install dependencies if needed
- [ ] Apply migrations

```powershell
python manage.py migrate
```

- [ ] Start the app locally if testing on local

```powershell
python manage.py runserver
```

- [ ] Confirm these test accounts or equivalent data exist:
  - [ ] superadmin
  - [ ] clinic owner on `Free`
  - [ ] clinic owner on `Premium`
  - [ ] Front Desk staff
  - [ ] Doctor staff
  - [ ] Nurse staff
  - [ ] patient portal account
  - [ ] booked patient without portal account
  - [ ] clinic with active subscription
  - [ ] clinic with inactive subscription

## 2. Automated Checks

- [ ] Run unit tests

```powershell
python manage.py test core
```

- [ ] Run Django checks

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
```

- [ ] Run deploy checks with production-shaped values

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

## 3. Auth And Recovery UI

Review these pages for layout, branding, spacing, hero split, and form usability:

- [ ] `/accounts/login/`
- [ ] `/signup/`
- [ ] `/accounts/password_reset/`
- [ ] `/accounts/password_reset/done/`
- [ ] `/resend-verification/`
- [ ] `/accounts/2fa/setup/`
- [ ] `/accounts/2fa/verify/`
- [ ] `/accounts/password_change/`

Validate:

- [ ] flat branded header is consistent
- [ ] left hero occupies the correct half-screen width on desktop
- [ ] right form panel is the only desktop scroll area
- [ ] form fields align cleanly
- [ ] password eye toggle works on all password inputs
- [ ] Google sign-in button renders correctly
- [ ] top nav CTA buttons work

Functional checks:

- [ ] valid login succeeds
- [ ] invalid login shows a clear error
- [ ] password reset request completes without template errors
- [ ] password reset email arrives
- [ ] password reset confirmation email arrives after password change
- [ ] resend verification flow works
- [ ] active user can reset password
- [ ] inactive or unverified flow remains safe

## 4. Two-Factor Authentication

- [ ] superadmin login requires 2FA
- [ ] 2FA setup page shows QR code and manual key
- [ ] valid TOTP code verifies successfully
- [ ] invalid TOTP code shows an error
- [ ] recovery codes are generated
- [ ] recovery code login works
- [ ] used recovery code cannot be reused
- [ ] clinic admin can enable optional 2FA from settings
- [ ] disable 2FA flow works
- [ ] related audit/security events appear

## 5. Plan, Signup, And Billing

- [ ] `Free` and `Premium` plans are visible and understandable on `/signup/`
- [ ] new clinic owner signup sends platform alert email
- [ ] `Free` activation completes without PayPal
- [ ] `Premium` activation completes with PayPal sandbox flow
- [ ] plan activation sends platform alert email
- [ ] billing page shows current plan correctly
- [ ] `Free` usage-left counters render
- [ ] `Premium` displays unlimited behavior correctly

Free enforcement:

- [ ] block staff creation after limit
- [ ] block service creation after limit
- [ ] block appointments after monthly limit
- [ ] upgrade messaging is visible and understandable

Premium validation:

- [ ] additional staff creation allowed
- [ ] additional services allowed
- [ ] additional appointments allowed

## 6. Portal Shell And Navigation

Desktop and tablet:

- [ ] top bar layout is correct
- [ ] search is usable
- [ ] notification bell renders correctly
- [ ] messages icon renders correctly
- [ ] avatar/profile menu works
- [ ] expanded sidebar layout is correct
- [ ] collapsed sidebar layout is correct
- [ ] collapsed tooltips appear in front of content
- [ ] collapse/expand button aligns correctly
- [ ] sidebar utility accordion works

Mobile:

- [ ] drawer opens and closes
- [ ] nav items remain accessible
- [ ] profile and notification actions remain usable

## 7. Dashboard And Core Portal Pages

- [ ] `/dashboard/` loads for owner and staff
- [ ] `/calendar/` loads
- [ ] `/staff/` loads
- [ ] `/services/` loads
- [ ] `/patients/` loads
- [ ] `/appointments/` loads
- [ ] `/billing/` loads
- [ ] `/settings/` loads
- [ ] `/security-audit/` loads for allowed role only
- [ ] `/messages/` loads for allowed role only

Validate for each page:

- [ ] current tab title is correct in top bar
- [ ] layout does not overflow
- [ ] cards and actions are aligned
- [ ] empty states are readable

## 8. Staff, Service, And Appointment Management

Staff:

- [ ] add staff works
- [ ] edit staff works
- [ ] active staff creation sends welcome email
- [ ] inactive staff creation sends verification email
- [ ] invalid email format is blocked

Services:

- [ ] add service works
- [ ] edit service works
- [ ] service modal fits laptop screens

Appointments:

- [ ] create appointment works
- [ ] update appointment works
- [ ] history view works
- [ ] appointment details are correct
- [ ] notification is created for relevant changes

## 9. Public Booking, Manage, And Waitlist

Booking page:

- [ ] `/clinic/<slug>/` uses the refreshed branded shell
- [ ] service change refreshes slots
- [ ] booking form validates correctly
- [ ] booking success page renders correctly
- [ ] confirmation code appears
- [ ] booking confirmation email arrives

Lookup and manage:

- [ ] `/appointments/lookup/` matches booking email + confirmation code
- [ ] valid appointment opens manage page
- [ ] invalid values show safe error
- [ ] patient can cancel from manage link
- [ ] patient can reschedule from manage link
- [ ] patient can send clinic message from manage page

Waitlist:

- [ ] waitlist form appears when no slots are available
- [ ] waitlist submission succeeds
- [ ] waitlist appears in staff waitlist page

Embed:

- [ ] embed iframe loads on external page
- [ ] embedded booking can submit successfully
- [ ] embed success state works

## 10. Patient Signup And Portal

- [ ] `/clinic/<slug>/patient-signup/` uses the refreshed shell
- [ ] patient signup succeeds for new patient
- [ ] patient verification messaging is correct
- [ ] existing patient linking behavior is correct
- [ ] patient portal login succeeds
- [ ] patient portal layout works

## 11. Messaging

Owner controls:

- [ ] clinic owner can open messaging permissions in settings
- [ ] owner can set role access for:
  - [ ] Admin
  - [ ] Doctor
  - [ ] Nurse
  - [ ] FrontDesk
- [ ] settings save correctly

Staff inbox:

- [ ] allowed role can open `/messages/`
- [ ] disallowed role is blocked
- [ ] unread count shows in top nav
- [ ] message preview dropdown works
- [ ] opening a thread shows full conversation history
- [ ] staff reply stays in the same thread
- [ ] patient reply stays in the same thread
- [ ] owner/clinic email receives patient message alert

Patient side:

- [ ] patient can start a new portal thread
- [ ] patient can reply to existing thread
- [ ] staff reply sends patient email notice
- [ ] appointment-manage message joins the same appointment thread

## 12. Notifications And Search

Notifications:

- [ ] bell badge updates
- [ ] preview dropdown works
- [ ] clicking notification opens destination
- [ ] opening notification marks it read

Search:

- [ ] top search is visible for allowed roles
- [ ] exact confirmation code redirects directly
- [ ] grouped preview dropdown appears
- [ ] appointments and patients are grouped correctly
- [ ] search results page works
- [ ] role scoping prevents unauthorized results

## 13. Security And Audit

Security guard:

- [ ] repeated failed logins trigger rate limit
- [ ] branded guard page renders cleanly
- [ ] security alert email goes to `SECURITY_ALERT_EMAILS` or superuser fallback

Audit:

- [ ] clinic access activity appears in settings
- [ ] full security audit page loads for clinic admin
- [ ] user filter works
- [ ] role filter works
- [ ] event filter works
- [ ] date filter works
- [ ] IP is captured
- [ ] country is captured when proxy header is available

Access rules:

- [ ] admin can add IP whitelist rule
- [ ] admin can add IP block rule
- [ ] admin can add country block rule

## 14. Email Deliverability

Validate real delivery for:

- [ ] verification email
- [ ] resend verification email
- [ ] staff welcome email
- [ ] password reset email
- [ ] password reset confirmation email
- [ ] platform signup alert email
- [ ] platform plan activation alert email
- [ ] security alert email
- [ ] patient messaging notice email
- [ ] clinic messaging alert email

Check:

- [ ] sender uses correct `DEFAULT_FROM_EMAIL`
- [ ] messages are not landing in spam for test accounts
- [ ] subject lines and branding are correct

## 15. Public Page Visual Consistency

Review these pages for consistent shell, color, spacing, and hero behavior:

- [ ] login
- [ ] signup
- [ ] password reset
- [ ] password reset done
- [ ] resend verification
- [ ] booking
- [ ] booking success
- [ ] patient signup
- [ ] patient signup success
- [ ] appointment lookup
- [ ] appointment manage
- [ ] 404
- [ ] security guard
- [ ] 2FA setup
- [ ] 2FA verify
- [ ] recovery pages

Confirm:

- [ ] no stray hero chips remain where they were removed
- [ ] no old green shell remains
- [ ] left hero width is consistent
- [ ] mobile stack behavior is consistent

## 16. Responsive QA

Check these viewport sizes:

- [ ] `390 x 844`
- [ ] `768 x 1024`
- [ ] `1024 x 768`
- [ ] `1440 x 900`

Review:

- [ ] login
- [ ] signup
- [ ] password reset
- [ ] booking
- [ ] patient signup
- [ ] dashboard
- [ ] appointments
- [ ] staff
- [ ] services
- [ ] billing
- [ ] settings
- [ ] messages

Validate:

- [ ] no horizontal overflow
- [ ] only intended panels scroll
- [ ] buttons stay visible
- [ ] forms remain readable
- [ ] sidebars/drawers are usable
- [ ] modals fit the viewport

## 17. Regression Watch List

- [ ] broken redirects after submit
- [ ] missing CSRF token errors
- [ ] old layout unexpectedly rendering
- [ ] duplicate emails being sent
- [ ] duplicate notifications being created
- [ ] stale unread counts
- [ ] message thread splitting unexpectedly
- [ ] PayPal activation mismatch
- [ ] search preview overlay clipping
- [ ] collapsed sidebar tooltip clipping
- [ ] 2FA session handoff issues

## 18. Release Sign-off

- [ ] automated checks passed
- [ ] manual smoke test passed
- [ ] UI/UX review passed
- [ ] responsive review passed
- [ ] email delivery review passed
- [ ] billing sandbox test passed
- [ ] no blocker bugs found

Tested by: __________________

Date tested: __________________

Notes:

- ________________________________________
- ________________________________________
- ________________________________________
