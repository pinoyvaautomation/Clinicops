# ClinicOps Launch Checklist

Use this checklist before launching the current `Free + Premium` version of ClinicOps.

## 1. Plan Setup

- Confirm only `2 active plans` exist in `/admin/` -> `Plans`
- Confirm `Free` has:
  - `is_free = true`
  - `price_cents = 0`
  - blank `paypal_plan_id`
  - `staff_limit = 2`
  - `service_limit = 3`
  - `monthly_appointment_limit = 50`
  - `includes_reminders = false`
  - `includes_notifications = true`
  - `includes_custom_branding = false`
- Confirm `Premium` has:
  - `is_free = false`
  - real `paypal_plan_id`
  - paid `price_cents`
  - blank limit fields for unlimited use
  - `includes_reminders = true`
  - `includes_notifications = true`
  - `includes_custom_branding = true`
- Confirm any old or test plans are set to inactive

## 2. Signup Flow

- Open `/signup/`
- Confirm both `Free` and `Premium` are visible and understandable
- Select `Free`
- Complete clinic account creation
- Confirm activation succeeds without PayPal
- Confirm the user lands in the portal
- Log out and log back in
- Confirm the clinic remains active on Free

- Repeat using `Premium`
- Confirm PayPal checkout and subscription approval work
- Confirm the clinic becomes active after the paid activation step
- Confirm billing shows the correct paid plan

## 3. Billing

- Log in as a `Free` clinic admin
- Open `/billing/`
- Confirm current plan shows `Free`
- Confirm usage-left counters are visible:
  - staff left
  - services left
  - appointments left
- Confirm upgrade messaging is visible

- Log in as a `Premium` clinic admin
- Open `/billing/`
- Confirm current plan shows `Premium`
- Confirm unlimited usage displays correctly
- Confirm PayPal sync/control actions only appear for paid plans

## 4. Free Limit Enforcement

- For a Free clinic, create `2` staff successfully
- Try to create a `3rd` staff member
- Confirm the action is blocked with a clear upgrade message

- Create `3` services successfully
- Try to create a `4th` service
- Confirm the action is blocked with a clear upgrade message

- Create appointments until reaching the monthly limit
- Try to create one more appointment
- Confirm the action is blocked with a clear upgrade message

- Confirm reminders are not sent for Free
- Confirm premium-only branding/custom settings are unavailable or hidden

## 5. Premium Behavior

- For a Premium clinic, create more than `2` staff
- Create more than `3` services
- Create more than `50` appointments in the month
- Confirm no plan-limit block appears
- Confirm reminders work
- Confirm notifications work
- Confirm premium-only settings are available

## 6. Notifications

- Create or update staff
- Create or update services
- Create or update appointments
- Confirm the bell badge updates
- Confirm the notification preview dropdown works
- Click a notification
- Confirm it opens the correct record
- Confirm it marks as read automatically

## 7. Search

- Search an exact confirmation code
- Confirm direct redirect to the correct appointment
- Search by patient name
- Confirm results show matching appointments and patients
- Test with `FrontDesk`
- Test with `Doctor`
- Confirm each role only sees allowed records

## 8. Responsive QA

- Desktop:
  - dashboard
  - billing
  - appointments
  - notifications
  - signup
- Tablet:
  - sidebar scroll behavior
  - top search usability
  - account/settings visibility
- Mobile:
  - login form first
  - signup form first
  - drawer navigation
  - readable billing and plan selection

## 9. Security And Billing

- Confirm `PAYPAL_VERIFY_WEBHOOK = true` in production
- Confirm `PAYPAL_WEBHOOK_ID` is set
- Confirm duplicate webhook delivery does not break subscription state
- Confirm Google sign-in only works for existing accounts
- Confirm inactive accounts are still blocked

## 10. Final Release Readiness

- Run `python manage.py test core`
- Run `python manage.py check --deploy`
- Confirm migrations are applied
- Confirm no test/sample plans are visible to real users
- Confirm real Render env values are set
- Confirm the production domain and callback URLs are correct

## 11. Recommended Test Clinics

- Create one dedicated `Free` test clinic
- Create one dedicated `Premium` test clinic
- Complete this checklist with both before launch
