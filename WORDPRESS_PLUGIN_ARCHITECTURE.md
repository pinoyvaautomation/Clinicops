# ClinicOps WordPress MVP Architecture

This document defines the recommended WordPress architecture for moving ClinicOps from a Django SaaS MVP to a WordPress-based product.

It is intentionally split into:

- WordPress core responsibilities
- custom plugin responsibilities
- modular plugin folder structure
- custom database schema
- module boundaries for scale

This is the clean target:

- WordPress core handles authentication, roles, media, admin shell, REST bootstrap, cron, and settings APIs
- the `clinicops-core` plugin owns clinic operations and business logic
- billing stays in an integration module, not mixed into appointments or patients

## Architecture Rule

Do not build the clinic app as a mix of:

- posts
- post meta
- random options
- admin-ajax handlers

Use:

- WordPress users and roles for identity
- custom plugin tables for operational records
- REST controllers for app endpoints
- service/repository modules for business logic

That is the only structure that scales cleanly for a multi-user clinic product.

## Ownership Map

| Layer | Owns |
|---|---|
| WordPress Core | `wp_users`, `wp_usermeta`, capabilities, uploads/media, plugin loading, cron, options/settings, REST bootstrap |
| `clinicops-core` plugin | clinics, staff, patients, appointments, waitlist, messaging, notifications, support, audit/security, entitlement checks |
| Billing integration module | subscription sync, plan entitlements, invoice/subscription events |

## Standard WordPress Plugin Structure

This follows WordPress plugin conventions:

- one main plugin file with the plugin header
- plugin lives in its own folder
- activation/deactivation hooks in the main plugin
- uninstall handler at root if needed
- code organized into subfolders

Recommended production structure:

```text
wp-content/plugins/clinicops-core/
  clinicops-core.php
  uninstall.php
  readme.txt
  composer.json
  README.md
  languages/
  assets/
    admin/
      css/
      js/
      images/
    public/
      css/
      js/
      images/
  templates/
    admin/
    portal/
    emails/
  config/
    plugin.php
    capabilities.php
    tables.php
    routes.php
  src/
    Bootstrap.php
    Container.php
    Support/
      Helpers/
      Http/
      Validation/
      Encryption/
      Logging/
    Domain/
      Shared/
        Contracts/
        ValueObjects/
        Exceptions/
    Modules/
      Clinics/
        Module.php
        Domain/
        Application/
        Infrastructure/
        Interfaces/
      Staff/
        Module.php
        Domain/
        Application/
        Infrastructure/
        Interfaces/
      Patients/
        Module.php
        Domain/
        Application/
        Infrastructure/
        Interfaces/
      AppointmentTypes/
        Module.php
        Domain/
        Application/
        Infrastructure/
        Interfaces/
      Appointments/
        Module.php
        Domain/
        Application/
        Infrastructure/
        Interfaces/
      Waitlist/
        Module.php
        Domain/
        Application/
        Infrastructure/
        Interfaces/
      Messaging/
        Module.php
        Domain/
        Application/
        Infrastructure/
        Interfaces/
      Notifications/
        Module.php
        Domain/
        Application/
        Infrastructure/
        Interfaces/
      Security/
        Module.php
        Domain/
        Application/
        Infrastructure/
        Interfaces/
      Support/
        Module.php
        Domain/
        Application/
        Infrastructure/
        Interfaces/
      Billing/
        Module.php
        Domain/
        Application/
        Infrastructure/
        Interfaces/
    Infrastructure/
      Database/
        Installer.php
        Migrator.php
        Tables/
      WordPress/
        Hooks/
        Cron/
        Capabilities/
        Settings/
      Integrations/
        SureCart/
  tests/
    Unit/
    Integration/
```

## Why This Structure

The WordPress handbook recommends:

- a dedicated plugin folder
- a single root plugin file
- subfolders for grouped code/assets

The exact `src/Modules/*` structure is the scaling recommendation here, not a WordPress requirement. It is the modular layer that keeps a SaaS MVP from collapsing into one giant `includes/` directory.

## Module Standard

Each module should own one bounded area of behavior.

Example:

```text
src/Modules/Appointments/
  Module.php
  Domain/
    Appointment.php
    AppointmentRepository.php
    AppointmentPolicy.php
  Application/
    CreateAppointment.php
    CancelAppointment.php
    ListAppointments.php
    AppointmentValidator.php
  Infrastructure/
    DatabaseAppointmentRepository.php
    AppointmentTable.php
    AppointmentCron.php
  Interfaces/
    Rest/
      AppointmentController.php
    Admin/
      AppointmentAdminPage.php
```

### Module Rules

- `Domain/` contains business rules and contracts, not WordPress glue
- `Application/` contains use cases and orchestrators
- `Infrastructure/` contains MySQL, WordPress hooks, cron, and third-party adapters
- `Interfaces/` contains REST controllers, admin pages, and template presenters
- `Module.php` registers hooks/routes/services for only that module

This keeps the plugin maintainable when the product grows.

## Bootstrap Flow

```text
clinicops-core.php
  -> src/Bootstrap.php
  -> load config
  -> register activation/deactivation hooks
  -> boot container
  -> boot module providers
  -> register REST routes
  -> register cron hooks
  -> register admin pages
```

## WordPress-Specific Development Standards

For this project, use these WordPress conventions:

- main plugin header only in `clinicops-core.php`
- `register_activation_hook()` for table creation and default settings
- `register_deactivation_hook()` for cron cleanup only
- `uninstall.php` only for explicit data cleanup policy
- `dbDelta()` for custom table creation and upgrades
- `register_rest_route()` for app endpoints
- capability checks in `permission_callback`
- `wp_schedule_event()` or `wp_schedule_single_event()` for async jobs
- avoid hard-coded plugin paths; use WordPress path helpers

## Custom Data Schema

### Identity and tenancy

#### `wp_clinicops_clinics`

- `id` BIGINT unsigned PK
- `name` VARCHAR(255) not null
- `slug` VARCHAR(255) unique not null
- `timezone` VARCHAR(64) not null default `UTC`
- `email_enc` LONGTEXT null
- `email_hash` CHAR(64) null
- `phone_enc` LONGTEXT null
- `phone_hash` CHAR(64) null
- `owner_user_id` BIGINT unsigned null
- `logo_attachment_id` BIGINT unsigned null
- `brand_color` CHAR(7) not null default `#1d4ed8`
- `is_active` TINYINT(1) not null default `1`
- `created_at_gmt` DATETIME not null

#### `wp_clinicops_staff`

- `id` BIGINT unsigned PK
- `clinic_id` BIGINT unsigned not null
- `user_id` BIGINT unsigned not null
- `staff_role` VARCHAR(32) not null
- `avatar_attachment_id` BIGINT unsigned null
- `is_active` TINYINT(1) not null default `1`
- `created_at_gmt` DATETIME not null

#### `wp_clinicops_patients`

- `id` BIGINT unsigned PK
- `clinic_id` BIGINT unsigned not null
- `user_id` BIGINT unsigned null
- `avatar_attachment_id` BIGINT unsigned null
- `first_name_enc` LONGTEXT not null
- `last_name_enc` LONGTEXT not null
- `email_enc` LONGTEXT not null
- `email_hash` CHAR(64) not null
- `phone_enc` LONGTEXT not null
- `phone_hash` CHAR(64) not null
- `dob_enc` LONGTEXT null
- `created_at_gmt` DATETIME not null

### Scheduling

#### `wp_clinicops_appointment_types`

- `id` BIGINT unsigned PK
- `clinic_id` BIGINT unsigned not null
- `name` VARCHAR(100) not null
- `duration_minutes` INT unsigned not null default `30`
- `price_cents` INT unsigned null
- `is_active` TINYINT(1) not null default `1`
- `created_at_gmt` DATETIME not null

#### `wp_clinicops_appointments`

- `id` BIGINT unsigned PK
- `clinic_id` BIGINT unsigned not null
- `appointment_type_id` BIGINT unsigned null
- `staff_id` BIGINT unsigned not null
- `patient_id` BIGINT unsigned not null
- `start_at_gmt` DATETIME not null
- `end_at_gmt` DATETIME not null
- `status` VARCHAR(16) not null default `scheduled`
- `notes_enc` LONGTEXT null
- `intake_reason_enc` LONGTEXT null
- `intake_details_enc` LONGTEXT null
- `consent_to_treatment` TINYINT(1) not null default `0`
- `consent_to_privacy` TINYINT(1) not null default `0`
- `consent_signature_name_enc` LONGTEXT null
- `consent_signed_at_gmt` DATETIME null
- `cancel_reason_enc` LONGTEXT null
- `confirmation_code` VARCHAR(12) unique null
- `reminder_sent_at_gmt` DATETIME null
- `created_at_gmt` DATETIME not null

Business rules:

- no overlap for the same staff member
- `end_at_gmt > start_at_gmt`
- appointment staff/patient/type must belong to the same clinic
- confirmation code must be unique

#### `wp_clinicops_waitlist`

- `id` BIGINT unsigned PK
- `clinic_id` BIGINT unsigned not null
- `appointment_type_id` BIGINT unsigned null
- `patient_id` BIGINT unsigned null
- `first_name_enc` LONGTEXT not null
- `last_name_enc` LONGTEXT not null
- `email_enc` LONGTEXT not null
- `email_hash` CHAR(64) not null
- `phone_enc` LONGTEXT not null
- `phone_hash` CHAR(64) not null
- `preferred_start_date` DATE null
- `preferred_end_date` DATE null
- `notes_enc` LONGTEXT null
- `consent_to_contact` TINYINT(1) not null default `1`
- `status` VARCHAR(16) not null default `active`
- `created_at_gmt` DATETIME not null

### Messaging and notifications

#### `wp_clinicops_notifications`

- `id` BIGINT unsigned PK
- `clinic_id` BIGINT unsigned null
- `recipient_user_id` BIGINT unsigned not null
- `actor_user_id` BIGINT unsigned null
- `event_type` VARCHAR(64) not null
- `level` VARCHAR(16) not null default `info`
- `title` VARCHAR(140) not null
- `body` LONGTEXT null
- `link` VARCHAR(255) null
- `metadata_json` LONGTEXT null
- `is_read` TINYINT(1) not null default `0`
- `read_at_gmt` DATETIME null
- `created_at_gmt` DATETIME not null

#### `wp_clinicops_messaging_permissions`

- `id` BIGINT unsigned PK
- `clinic_id` BIGINT unsigned not null
- `role` VARCHAR(32) not null
- `access_level` VARCHAR(16) not null default `none`
- `updated_at_gmt` DATETIME not null

#### `wp_clinicops_message_threads`

- `id` BIGINT unsigned PK
- `clinic_id` BIGINT unsigned not null
- `patient_id` BIGINT unsigned not null
- `appointment_id` BIGINT unsigned null
- `subject` VARCHAR(140) null
- `source` VARCHAR(16) not null default `portal`
- `status` VARCHAR(16) not null default `open`
- `last_message_sender_type` VARCHAR(16) not null default `patient`
- `last_message_excerpt` VARCHAR(180) null
- `last_message_at_gmt` DATETIME not null
- `created_at_gmt` DATETIME not null
- `updated_at_gmt` DATETIME not null

#### `wp_clinicops_messages`

- `id` BIGINT unsigned PK
- `thread_id` BIGINT unsigned not null
- `sender_user_id` BIGINT unsigned null
- `sender_type` VARCHAR(16) not null
- `sender_label` VARCHAR(150) null
- `body_enc` LONGTEXT not null
- `created_at_gmt` DATETIME not null

#### `wp_clinicops_message_thread_reads`

- `id` BIGINT unsigned PK
- `thread_id` BIGINT unsigned not null
- `user_id` BIGINT unsigned not null
- `last_read_at_gmt` DATETIME not null

### Billing projection

Keep billing isolated. The clinic app should only mirror what it needs for entitlement checks.

#### `wp_clinicops_plan_entitlements`

- `id` BIGINT unsigned PK
- `plan_key` VARCHAR(64) unique not null
- `label` VARCHAR(100) not null
- `billing_provider` VARCHAR(32) not null
- `external_product_id` VARCHAR(64) not null
- `external_price_id` VARCHAR(64) not null
- `billing_interval` VARCHAR(16) not null
- `is_free` TINYINT(1) not null default `0`
- `staff_limit` INT unsigned null
- `service_limit` INT unsigned null
- `monthly_appointment_limit` INT unsigned null
- `includes_reminders` TINYINT(1) not null default `1`
- `includes_notifications` TINYINT(1) not null default `1`
- `includes_messaging` TINYINT(1) not null default `1`
- `includes_waitlist` TINYINT(1) not null default `1`
- `includes_custom_branding` TINYINT(1) not null default `1`
- `is_active` TINYINT(1) not null default `1`
- `created_at_gmt` DATETIME not null
- `updated_at_gmt` DATETIME not null

#### `wp_clinicops_subscriptions`

- `id` BIGINT unsigned PK
- `clinic_id` BIGINT unsigned not null
- `owner_user_id` BIGINT unsigned null
- `plan_entitlement_id` BIGINT unsigned null
- `billing_provider` VARCHAR(32) not null
- `external_customer_id` VARCHAR(64) not null
- `external_subscription_id` VARCHAR(64) unique not null
- `external_product_id` VARCHAR(64) not null
- `external_price_id` VARCHAR(64) not null
- `status` VARCHAR(20) not null
- `started_at_gmt` DATETIME null
- `current_period_end_gmt` DATETIME null
- `cancel_at_period_end` TINYINT(1) not null default `0`
- `last_event_type` VARCHAR(64) null
- `created_at_gmt` DATETIME not null
- `updated_at_gmt` DATETIME not null

#### `wp_clinicops_billing_events`

- `id` BIGINT unsigned PK
- `billing_provider` VARCHAR(32) not null
- `event_id` VARCHAR(128) unique not null
- `event_type` VARCHAR(128) not null
- `resource_type` VARCHAR(64) null
- `resource_id` VARCHAR(128) null
- `status` VARCHAR(20) not null default `received`
- `payload_json` LONGTEXT not null
- `error_message` LONGTEXT null
- `subscription_id` BIGINT unsigned null
- `received_at_gmt` DATETIME not null
- `processed_at_gmt` DATETIME null

### Support and security

#### `wp_clinicops_help_requests`

- `id` BIGINT unsigned PK
- `clinic_id` BIGINT unsigned not null
- `submitted_by_user_id` BIGINT unsigned null
- `request_type` VARCHAR(16) not null
- `status` VARCHAR(16) not null default `new`
- `category` VARCHAR(32) null
- `priority` VARCHAR(16) not null default `medium`
- `subject` VARCHAR(140) not null
- `details_enc` LONGTEXT not null
- `business_impact_enc` LONGTEXT null
- `page_url` VARCHAR(255) null
- `user_agent` VARCHAR(255) null
- `reporter_name` VARCHAR(150) null
- `reporter_email` VARCHAR(254) null
- `staff_role` VARCHAR(32) null
- `internal_notes_enc` LONGTEXT null
- `resolved_at_gmt` DATETIME null
- `created_at_gmt` DATETIME not null
- `updated_at_gmt` DATETIME not null

#### `wp_clinicops_security_events`

- `id` BIGINT unsigned PK
- `clinic_id` BIGINT unsigned null
- `user_id` BIGINT unsigned null
- `event_type` VARCHAR(64) not null
- `identifier` VARCHAR(254) null
- `ip_address` VARCHAR(64) null
- `country_code` CHAR(2) null
- `user_agent` VARCHAR(255) null
- `request_path` VARCHAR(255) null
- `metadata_json` LONGTEXT null
- `created_at_gmt` DATETIME not null

#### `wp_clinicops_security_access_rules`

- `id` BIGINT unsigned PK
- `name` VARCHAR(100) not null
- `action` VARCHAR(16) not null
- `target_type` VARCHAR(16) not null
- `scope` VARCHAR(16) not null default `auth`
- `value` VARCHAR(64) not null
- `note` LONGTEXT null
- `is_active` TINYINT(1) not null default `1`
- `created_at_gmt` DATETIME not null
- `updated_at_gmt` DATETIME not null

#### `wp_clinicops_two_factor_recovery_codes`

- `id` BIGINT unsigned PK
- `user_id` BIGINT unsigned not null
- `code_hash` CHAR(64) not null
- `code_suffix` VARCHAR(6) not null
- `consumed_at_gmt` DATETIME null
- `created_at_gmt` DATETIME not null

## Indexing Rules

Minimum indexing strategy:

- tenant-scoped tables index `clinic_id`
- timeline tables index `(clinic_id, created_at_gmt)` or equivalent
- appointment lookups index `(staff_id, start_at_gmt)` and `(patient_id, start_at_gmt)`
- search fields use deterministic hash indexes, not plaintext
- billing event table has unique `event_id`

## What Should Not Be Custom Tables

Use WordPress-native objects only where they actually fit:

- `wp_users` for logins
- attachments for logos/avatars/files
- `options` for plugin-level settings
- `site options` for network-level settings in multisite

Do not use custom post types for:

- appointments
- patient records
- messages
- security events
- subscriptions

Those are app records, not editorial content.

## REST API Design

Namespace:

- `clinicops/v1`

Controller groups:

- `/clinics`
- `/staff`
- `/patients`
- `/appointment-types`
- `/appointments`
- `/waitlist`
- `/messages`
- `/notifications`
- `/support`
- `/security`
- `/billing`
- `/search`

Permission rule:

- every route gets a `permission_callback`
- business authorization stays in policies/services, not only the controller

## Recommended Build Order

1. Bootstrap plugin shell and Composer autoload
2. Create database installer and versioned migrations
3. Register roles and capabilities
4. Build tenancy module: clinics, staff, patients
5. Build scheduling module: appointment types, appointments, waitlist
6. Build messaging and notifications
7. Build support and security
8. Add billing projection and integration module
9. Add admin pages and REST controllers
10. Add import/migration scripts from Django data

## Final Recommendation

If the SaaS MVP is moving to WordPress, the scalable structure is:

- one product plugin
- custom tables for domain data
- modular `src/Modules/*` architecture
- WordPress APIs at the edges
- billing isolated behind an integration module

That gives you a real application architecture inside WordPress instead of a pile of hooks and meta fields.
