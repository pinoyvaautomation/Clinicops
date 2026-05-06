<?php

declare(strict_types=1);

namespace ClinicOps\Core;

use wpdb;

if (! defined('ABSPATH')) {
    exit;
}

final class Installer
{
    public static function install(): void
    {
        global $wpdb;

        require_once ABSPATH . 'wp-admin/includes/upgrade.php';

        foreach (self::schema_sql($wpdb) as $sql) {
            dbDelta($sql);
        }

        update_option('clinicops_core_db_version', CLINICOPS_CORE_VERSION, false);
    }

    /**
     * @return array<int, string>
     */
    private static function schema_sql(wpdb $wpdb): array
    {
        $charset_collate = $wpdb->get_charset_collate();
        $prefix = $wpdb->prefix . 'clinicops_';

        return [
            "CREATE TABLE {$prefix}clinics (
                id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
                name varchar(255) NOT NULL,
                slug varchar(255) NOT NULL,
                timezone varchar(64) NOT NULL DEFAULT 'UTC',
                email_enc longtext NULL,
                email_hash char(64) NULL,
                phone_enc longtext NULL,
                phone_hash char(64) NULL,
                owner_user_id bigint(20) unsigned NULL,
                logo_attachment_id bigint(20) unsigned NULL,
                brand_color char(7) NOT NULL DEFAULT '#1d4ed8',
                is_active tinyint(1) NOT NULL DEFAULT 1,
                created_at_gmt datetime NOT NULL,
                PRIMARY KEY  (id),
                UNIQUE KEY slug (slug),
                KEY owner_user_id (owner_user_id)
            ) $charset_collate;",

            "CREATE TABLE {$prefix}staff (
                id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
                clinic_id bigint(20) unsigned NOT NULL,
                user_id bigint(20) unsigned NOT NULL,
                staff_role varchar(32) NOT NULL,
                avatar_attachment_id bigint(20) unsigned NULL,
                is_active tinyint(1) NOT NULL DEFAULT 1,
                created_at_gmt datetime NOT NULL,
                PRIMARY KEY  (id),
                UNIQUE KEY user_id (user_id),
                KEY clinic_role_active (clinic_id, staff_role, is_active)
            ) $charset_collate;",

            "CREATE TABLE {$prefix}patients (
                id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
                clinic_id bigint(20) unsigned NOT NULL,
                user_id bigint(20) unsigned NULL,
                avatar_attachment_id bigint(20) unsigned NULL,
                first_name_enc longtext NOT NULL,
                last_name_enc longtext NOT NULL,
                email_enc longtext NOT NULL,
                email_hash char(64) NOT NULL,
                phone_enc longtext NOT NULL,
                phone_hash char(64) NOT NULL,
                dob_enc longtext NULL,
                created_at_gmt datetime NOT NULL,
                PRIMARY KEY  (id),
                KEY clinic_email_hash (clinic_id, email_hash),
                KEY clinic_phone_hash (clinic_id, phone_hash),
                KEY user_id (user_id)
            ) $charset_collate;",

            "CREATE TABLE {$prefix}appointment_types (
                id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
                clinic_id bigint(20) unsigned NOT NULL,
                name varchar(100) NOT NULL,
                duration_minutes int(10) unsigned NOT NULL DEFAULT 30,
                price_cents int(10) unsigned NULL,
                is_active tinyint(1) NOT NULL DEFAULT 1,
                created_at_gmt datetime NOT NULL,
                PRIMARY KEY  (id),
                UNIQUE KEY clinic_name (clinic_id, name)
            ) $charset_collate;",

            "CREATE TABLE {$prefix}appointments (
                id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
                clinic_id bigint(20) unsigned NOT NULL,
                appointment_type_id bigint(20) unsigned NULL,
                staff_id bigint(20) unsigned NOT NULL,
                patient_id bigint(20) unsigned NOT NULL,
                start_at_gmt datetime NOT NULL,
                end_at_gmt datetime NOT NULL,
                status varchar(16) NOT NULL DEFAULT 'scheduled',
                notes_enc longtext NULL,
                intake_reason_enc longtext NULL,
                intake_details_enc longtext NULL,
                consent_to_treatment tinyint(1) NOT NULL DEFAULT 0,
                consent_to_privacy tinyint(1) NOT NULL DEFAULT 0,
                consent_signature_name_enc longtext NULL,
                consent_signed_at_gmt datetime NULL,
                cancel_reason_enc longtext NULL,
                confirmation_code varchar(12) NULL,
                reminder_sent_at_gmt datetime NULL,
                created_at_gmt datetime NOT NULL,
                PRIMARY KEY  (id),
                UNIQUE KEY confirmation_code (confirmation_code),
                KEY clinic_start_at (clinic_id, start_at_gmt),
                KEY staff_start_at (staff_id, start_at_gmt),
                KEY patient_start_at (patient_id, start_at_gmt)
            ) $charset_collate;",

            "CREATE TABLE {$prefix}waitlist (
                id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
                clinic_id bigint(20) unsigned NOT NULL,
                appointment_type_id bigint(20) unsigned NULL,
                patient_id bigint(20) unsigned NULL,
                first_name_enc longtext NOT NULL,
                last_name_enc longtext NOT NULL,
                email_enc longtext NOT NULL,
                email_hash char(64) NOT NULL,
                phone_enc longtext NOT NULL,
                phone_hash char(64) NOT NULL,
                preferred_start_date date NULL,
                preferred_end_date date NULL,
                notes_enc longtext NULL,
                consent_to_contact tinyint(1) NOT NULL DEFAULT 1,
                status varchar(16) NOT NULL DEFAULT 'active',
                created_at_gmt datetime NOT NULL,
                PRIMARY KEY  (id),
                KEY clinic_status_created (clinic_id, status, created_at_gmt)
            ) $charset_collate;",

            "CREATE TABLE {$prefix}plan_entitlements (
                id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
                plan_key varchar(64) NOT NULL,
                label varchar(100) NOT NULL,
                surecart_product_id varchar(64) NOT NULL,
                surecart_price_id varchar(64) NOT NULL,
                billing_interval varchar(16) NOT NULL,
                is_free tinyint(1) NOT NULL DEFAULT 0,
                staff_limit int(10) unsigned NULL,
                service_limit int(10) unsigned NULL,
                monthly_appointment_limit int(10) unsigned NULL,
                includes_reminders tinyint(1) NOT NULL DEFAULT 1,
                includes_notifications tinyint(1) NOT NULL DEFAULT 1,
                includes_messaging tinyint(1) NOT NULL DEFAULT 1,
                includes_waitlist tinyint(1) NOT NULL DEFAULT 1,
                includes_custom_branding tinyint(1) NOT NULL DEFAULT 1,
                is_active tinyint(1) NOT NULL DEFAULT 1,
                created_at_gmt datetime NOT NULL,
                updated_at_gmt datetime NOT NULL,
                PRIMARY KEY  (id),
                UNIQUE KEY plan_key (plan_key)
            ) $charset_collate;",

            "CREATE TABLE {$prefix}subscriptions (
                id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
                clinic_id bigint(20) unsigned NOT NULL,
                owner_user_id bigint(20) unsigned NULL,
                plan_entitlement_id bigint(20) unsigned NULL,
                surecart_customer_id varchar(64) NOT NULL,
                surecart_subscription_id varchar(64) NOT NULL,
                surecart_product_id varchar(64) NOT NULL,
                surecart_price_id varchar(64) NOT NULL,
                status varchar(20) NOT NULL,
                started_at_gmt datetime NULL,
                current_period_end_gmt datetime NULL,
                cancel_at_period_end tinyint(1) NOT NULL DEFAULT 0,
                last_event_type varchar(64) NULL,
                created_at_gmt datetime NOT NULL,
                updated_at_gmt datetime NOT NULL,
                PRIMARY KEY  (id),
                UNIQUE KEY surecart_subscription_id (surecart_subscription_id),
                KEY clinic_status (clinic_id, status),
                KEY surecart_customer_id (surecart_customer_id)
            ) $charset_collate;",

            "CREATE TABLE {$prefix}surecart_events (
                id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
                event_id varchar(128) NOT NULL,
                event_type varchar(128) NOT NULL,
                resource_type varchar(64) NULL,
                resource_id varchar(128) NULL,
                status varchar(20) NOT NULL DEFAULT 'received',
                payload_json longtext NOT NULL,
                error_message longtext NULL,
                subscription_id bigint(20) unsigned NULL,
                received_at_gmt datetime NOT NULL,
                processed_at_gmt datetime NULL,
                PRIMARY KEY  (id),
                UNIQUE KEY event_id (event_id),
                KEY event_type (event_type)
            ) $charset_collate;",

            "CREATE TABLE {$prefix}notifications (
                id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
                clinic_id bigint(20) unsigned NULL,
                recipient_user_id bigint(20) unsigned NOT NULL,
                actor_user_id bigint(20) unsigned NULL,
                event_type varchar(64) NOT NULL,
                level varchar(16) NOT NULL DEFAULT 'info',
                title varchar(140) NOT NULL,
                body longtext NULL,
                link varchar(255) NULL,
                metadata_json longtext NULL,
                is_read tinyint(1) NOT NULL DEFAULT 0,
                read_at_gmt datetime NULL,
                created_at_gmt datetime NOT NULL,
                PRIMARY KEY  (id),
                KEY recipient_read_created (recipient_user_id, is_read, created_at_gmt),
                KEY clinic_created (clinic_id, created_at_gmt)
            ) $charset_collate;",

            "CREATE TABLE {$prefix}messaging_permissions (
                id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
                clinic_id bigint(20) unsigned NOT NULL,
                role varchar(32) NOT NULL,
                access_level varchar(16) NOT NULL DEFAULT 'none',
                updated_at_gmt datetime NOT NULL,
                PRIMARY KEY  (id),
                UNIQUE KEY clinic_role (clinic_id, role)
            ) $charset_collate;",

            "CREATE TABLE {$prefix}message_threads (
                id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
                clinic_id bigint(20) unsigned NOT NULL,
                patient_id bigint(20) unsigned NOT NULL,
                appointment_id bigint(20) unsigned NULL,
                subject varchar(140) NULL,
                source varchar(16) NOT NULL DEFAULT 'portal',
                status varchar(16) NOT NULL DEFAULT 'open',
                last_message_sender_type varchar(16) NOT NULL DEFAULT 'patient',
                last_message_excerpt varchar(180) NULL,
                last_message_at_gmt datetime NOT NULL,
                created_at_gmt datetime NOT NULL,
                updated_at_gmt datetime NOT NULL,
                PRIMARY KEY  (id),
                UNIQUE KEY appointment_id (appointment_id),
                KEY clinic_status_last (clinic_id, status, last_message_at_gmt),
                KEY patient_last (patient_id, last_message_at_gmt)
            ) $charset_collate;",

            "CREATE TABLE {$prefix}messages (
                id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
                thread_id bigint(20) unsigned NOT NULL,
                sender_user_id bigint(20) unsigned NULL,
                sender_type varchar(16) NOT NULL,
                sender_label varchar(150) NULL,
                body_enc longtext NOT NULL,
                created_at_gmt datetime NOT NULL,
                PRIMARY KEY  (id),
                KEY thread_created (thread_id, created_at_gmt)
            ) $charset_collate;",

            "CREATE TABLE {$prefix}message_thread_reads (
                id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
                thread_id bigint(20) unsigned NOT NULL,
                user_id bigint(20) unsigned NOT NULL,
                last_read_at_gmt datetime NOT NULL,
                PRIMARY KEY  (id),
                UNIQUE KEY thread_user (thread_id, user_id),
                KEY user_last_read (user_id, last_read_at_gmt)
            ) $charset_collate;",

            "CREATE TABLE {$prefix}help_requests (
                id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
                clinic_id bigint(20) unsigned NOT NULL,
                submitted_by_user_id bigint(20) unsigned NULL,
                request_type varchar(16) NOT NULL,
                status varchar(16) NOT NULL DEFAULT 'new',
                category varchar(32) NULL,
                priority varchar(16) NOT NULL DEFAULT 'medium',
                subject varchar(140) NOT NULL,
                details_enc longtext NOT NULL,
                business_impact_enc longtext NULL,
                page_url varchar(255) NULL,
                user_agent varchar(255) NULL,
                reporter_name varchar(150) NULL,
                reporter_email varchar(254) NULL,
                staff_role varchar(32) NULL,
                internal_notes_enc longtext NULL,
                resolved_at_gmt datetime NULL,
                created_at_gmt datetime NOT NULL,
                updated_at_gmt datetime NOT NULL,
                PRIMARY KEY  (id),
                KEY clinic_request_status_created (clinic_id, request_type, status, created_at_gmt),
                KEY submitted_by_created (submitted_by_user_id, created_at_gmt)
            ) $charset_collate;",

            "CREATE TABLE {$prefix}security_events (
                id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
                clinic_id bigint(20) unsigned NULL,
                user_id bigint(20) unsigned NULL,
                event_type varchar(64) NOT NULL,
                identifier varchar(254) NULL,
                ip_address varchar(64) NULL,
                country_code char(2) NULL,
                user_agent varchar(255) NULL,
                request_path varchar(255) NULL,
                metadata_json longtext NULL,
                created_at_gmt datetime NOT NULL,
                PRIMARY KEY  (id),
                KEY clinic_created (clinic_id, created_at_gmt),
                KEY user_created (user_id, created_at_gmt),
                KEY event_type_created (event_type, created_at_gmt)
            ) $charset_collate;",

            "CREATE TABLE {$prefix}security_access_rules (
                id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
                name varchar(100) NOT NULL,
                action varchar(16) NOT NULL,
                target_type varchar(16) NOT NULL,
                scope varchar(16) NOT NULL DEFAULT 'auth',
                value varchar(64) NOT NULL,
                note longtext NULL,
                is_active tinyint(1) NOT NULL DEFAULT 1,
                created_at_gmt datetime NOT NULL,
                updated_at_gmt datetime NOT NULL,
                PRIMARY KEY  (id),
                UNIQUE KEY unique_rule (action, target_type, scope, value),
                KEY scope_active (scope, is_active)
            ) $charset_collate;",

            "CREATE TABLE {$prefix}two_factor_recovery_codes (
                id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
                user_id bigint(20) unsigned NOT NULL,
                code_hash char(64) NOT NULL,
                code_suffix varchar(6) NOT NULL,
                consumed_at_gmt datetime NULL,
                created_at_gmt datetime NOT NULL,
                PRIMARY KEY  (id),
                KEY user_consumed (user_id, consumed_at_gmt)
            ) $charset_collate;",
        ];
    }
}
