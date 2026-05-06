<?php

declare(strict_types=1);

namespace ClinicOps\Core\Integrations;

use ClinicOps\Core\Services\Subscriptions_Service;
use WP_Error;
use WP_REST_Request;
use WP_REST_Response;

if (! defined('ABSPATH')) {
    exit;
}

final class SureCart_Webhooks
{
    public static function init(): void
    {
        add_action('clinicops_core_process_surecart_event', [self::class, 'process_event'], 10, 1);
    }

    public static function handle(WP_REST_Request $request)
    {
        $body = $request->get_body();
        $payload = json_decode($body, true);

        if (! is_array($payload)) {
            return new WP_Error('clinicops_invalid_webhook', 'SureCart webhook payload must be valid JSON.', ['status' => 400]);
        }

        if (! self::is_verified($request, $body)) {
            return new WP_Error('clinicops_invalid_signature', 'SureCart webhook signature verification failed.', ['status' => 401]);
        }

        $event_id = (string) ($payload['id'] ?? '');
        if ($event_id === '') {
            return new WP_Error('clinicops_missing_event_id', 'SureCart webhook event id is required.', ['status' => 400]);
        }

        global $wpdb;
        $table = $wpdb->prefix . 'clinicops_surecart_events';

        $existing_id = $wpdb->get_var(
            $wpdb->prepare("SELECT id FROM {$table} WHERE event_id = %s", $event_id)
        );

        if (! $existing_id) {
            $wpdb->insert($table, [
                'event_id' => $event_id,
                'event_type' => (string) ($payload['type'] ?? ''),
                'resource_type' => (string) ($payload['resource_type'] ?? ''),
                'resource_id' => (string) ($payload['resource_id'] ?? ''),
                'status' => 'received',
                'payload_json' => wp_json_encode($payload),
                'received_at_gmt' => current_time('mysql', true),
            ]);
            $existing_id = (int) $wpdb->insert_id;
        }

        wp_schedule_single_event(time() + 5, 'clinicops_core_process_surecart_event', [(int) $existing_id]);

        return new WP_REST_Response(['received' => true], 202);
    }

    public static function process_event(int $event_row_id): void
    {
        global $wpdb;
        $table = $wpdb->prefix . 'clinicops_surecart_events';

        $row = $wpdb->get_row(
            $wpdb->prepare("SELECT * FROM {$table} WHERE id = %d", $event_row_id),
            ARRAY_A
        );

        if (! is_array($row)) {
            return;
        }

        $payload = json_decode((string) $row['payload_json'], true);
        if (! is_array($payload)) {
            $wpdb->update($table, [
                'status' => 'failed',
                'error_message' => 'Stored webhook payload is not valid JSON.',
                'processed_at_gmt' => current_time('mysql', true),
            ], ['id' => $event_row_id]);
            return;
        }

        try {
            Subscriptions_Service::process_surecart_event($payload);
            $wpdb->update($table, [
                'status' => 'processed',
                'processed_at_gmt' => current_time('mysql', true),
            ], ['id' => $event_row_id]);
        } catch (\Throwable $exception) {
            $wpdb->update($table, [
                'status' => 'failed',
                'error_message' => $exception->getMessage(),
                'processed_at_gmt' => current_time('mysql', true),
            ], ['id' => $event_row_id]);
        }
    }

    private static function is_verified(WP_REST_Request $request, string $body): bool
    {
        if (defined('CLINICOPS_CORE_ALLOW_UNVERIFIED_SURECART_WEBHOOKS') && CLINICOPS_CORE_ALLOW_UNVERIFIED_SURECART_WEBHOOKS) {
            return true;
        }

        $signature = (string) $request->get_header('x-webhook-signature');
        $timestamp = (string) $request->get_header('x-webhook-timestamp');
        $secret = defined('CLINICOPS_CORE_SURECART_WEBHOOK_SECRET') ? (string) CLINICOPS_CORE_SURECART_WEBHOOK_SECRET : '';

        if ($signature === '' || $timestamp === '' || $secret === '') {
            return false;
        }

        /**
         * SureCart signature verification format should be implemented from the
         * official signed-payload spec before production use.
         */
        return (bool) apply_filters('clinicops_core_verify_surecart_webhook_signature', false, $signature, $timestamp, $body, $secret);
    }
}
