# ClinicOps to Base44 Migration Map

This repository now includes a Base44-ready entity scaffold in `base44/entities/`.

The important design rule is:

- Base44 should be driven from the current data model first.
- But the current Django app also has model validation, permissions, automation, and integration logic that are not expressed by the schema alone.

So the migration target is:

1. Mirror the Django schema in Base44 entities.
2. Extend the Base44 `User` entity with the access context Base44 needs.
3. Rebuild Django-only rules as Base44 backend functions and automations.

## What Was Generated

- `base44/entities/User.json`
- `base44/entities/*.json` for the current `core.models` entities

The entity files follow Base44's documented JSON Schema format and use:

- Base44 entity files for custom tables
- the built-in Base44 `User` entity extension for auth-related profile data
- string `*_id` fields instead of Django foreign keys

## Base44-Specific Mapping Decisions

### 1. Foreign keys become string IDs

Django relations such as `clinic = ForeignKey(Clinic)` become fields like:

- `clinic_id`
- `patient_id`
- `staff_id`
- `user_id`

That is the simplest shape for Base44 entities and keeps the meaning of the current database intact.

### 2. Django groups must become explicit data

ClinicOps currently derives staff role from Django groups, not from the `Staff` model itself.

That is not enough for Base44. To make permissions portable, the scaffold adds explicit role data:

- `Staff.staff_role`
- `User.account_type`
- `User.staff_role`

### 3. Encrypted Django fields are mapped as strings

The app currently uses `secured_fields` for patient, appointment, messaging, and support data.

Base44 entity schemas do not have a matching encrypted field type. Those fields are therefore mapped as normal `string` fields, and must be protected through:

- row-level security
- field-level security
- service-role backend functions for sensitive operations

### 4. The Base44 `User` entity is not enough on its own

Base44's built-in `User` entity gives you auth identity, but ClinicOps also needs clinic and profile context.

The scaffolded `User.json` adds fields for:

- `account_type`
- `default_clinic_id`
- `clinic_ids`
- `staff_id`
- `staff_role`
- `patient_profile_ids`
- `patient_clinic_ids`
- `avatar`
- two-factor status flags

This is deliberate. Current ClinicOps behavior includes:

- staff users linked to one clinic staff profile
- patient users possibly linked to multiple patient profiles across clinics

That multi-clinic patient behavior is visible in `core/context_processors.py` and needs explicit user-context data or service-role functions in Base44.

## Entity Coverage

The scaffold covers the active models in `core/models.py`:

- `Clinic`
- `AdminBranding`
- `Staff`
- `AppointmentType`
- `Patient`
- `Appointment`
- `WaitlistEntry`
- `Plan`
- `PromoCode`
- `PromoRedemption`
- `ClinicSubscription`
- `Notification`
- `ClinicMessagingPermission`
- `MessageThread`
- `Message`
- `MessageThreadReadState`
- `HelpRequest`
- `PayPalWebhookEvent`
- `SecurityEvent`
- `SecurityAccessRule`
- `TwoFactorRecoveryCode`

It does not scaffold Django Simple History tables. Those are operational audit tables created by Django, not first-class app models in `core/models.py`.

## Functions That Must Be Ported

These behaviors are not solved by schema alone and should become Base44 backend functions or automations.

### Booking and scheduling

Source files:

- `core/models.py`
- `core/booking.py`
- `core/tasks.py`

Needs to be ported:

- appointment overlap validation for the same staff member
- clinic consistency validation across appointment, patient, staff, and appointment type
- confirmation code generation
- available-slot calculation
- reminder sending automation

### Messaging

Source file:

- `core/messaging.py`

Needs to be ported:

- role-based messaging access
- appointment-linked thread creation
- patient portal thread creation
- unread/read-state tracking
- last message preview updates

### Billing and subscription sync

Source files:

- `core/paypal.py`
- `core/subscriptions.py`
- `core/plan_limits.py`

Needs to be ported:

- PayPal subscription sync
- webhook intake and idempotency
- plan usage calculations
- feature gating for reminders, messaging, waitlist, notifications, branding, staff limits, and service limits

### Security

Source files:

- `core/security.py`
- `core/middleware.py`
- `core/two_factor.py`
- `core/two_factor_middleware.py`
- `core/social_auth.py`

Needs to be ported:

- login throttling
- security access rules by IP and country
- security event logging
- two-factor verification and recovery code flows
- Google/social sign-in restrictions for existing local users

## Validation Rules That Need Functions

Base44 entity schemas mirror field shape and basic validation, but these current Django rules need explicit backend logic:

- unique slug generation for `Clinic`
- unique appointment confirmation codes
- `Appointment.end_at > Appointment.start_at`
- one thread per appointment when `appointment_id` is present
- unique `PromoRedemption` per clinic and promo code
- unique `ClinicMessagingPermission` per clinic and role
- unique `MessageThreadReadState` per thread and user
- unique `SecurityAccessRule` by action, target type, scope, and value
- `PromoCode` price and date-window validation
- `SecurityAccessRule` IP or country normalization

## Recommended Base44 Import Order

1. `Clinic`
2. Base44 auth users plus `User.json` custom fields
3. `Staff`
4. `Patient`
5. `AppointmentType`
6. `Plan`
7. `PromoCode`
8. `ClinicSubscription`
9. `PromoRedemption`
10. `Appointment`
11. `WaitlistEntry`
12. `ClinicMessagingPermission`
13. `MessageThread`
14. `Message`
15. `MessageThreadReadState`
16. `Notification`
17. `HelpRequest`
18. `PayPalWebhookEvent`
19. `SecurityEvent`
20. `SecurityAccessRule`
21. `TwoFactorRecoveryCode`
22. `AdminBranding`

## Practical Conclusion

Yes: Base44 should be built from the current database model.

But not from the schema alone.

For ClinicOps, the safe migration formula is:

- schema from `core.models`
- access model from Django groups and patient profile relationships
- backend functions from booking, billing, messaging, security, and reminder logic

That is the minimum needed to preserve behavior rather than only preserve tables.
