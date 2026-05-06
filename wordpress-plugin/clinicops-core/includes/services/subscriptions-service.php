<?php

declare(strict_types=1);

namespace ClinicOps\Core\Services;

use wpdb;

if (! defined('ABSPATH')) {
    exit;
}

final class Subscriptions_Service
{
    /**
     * @param array<string, mixed> $event
     */
    public static function process_surecart_event(array $event): void
    {
        $type = (string) ($event['type'] ?? '');
        $data = is_array($event['data'] ?? null) ? $event['data'] : [];
        $subscription = is_array($data['subscription'] ?? null) ? $data['subscription'] : $data;

        if (! in_array($type, [
            'subscription.created',
            'subscription.updated',
            'subscription.made_active',
            'subscription.canceled',
            'subscription.renewed',
        ], true)) {
            return;
        }

        self::upsert_projection($type, $subscription);
    }

    /**
     * @param array<string, mixed> $subscription
     */
    public static function upsert_projection(string $event_type, array $subscription): void
    {
        global $wpdb;

        $table = $wpdb->prefix . 'clinicops_subscriptions';
        $meta = is_array($subscription['metadata'] ?? null) ? $subscription['metadata'] : [];

        $clinic_id = absint($meta['clinic_id'] ?? 0);
        $owner_user_id = absint($meta['owner_user_id'] ?? 0);
        $surecart_subscription_id = (string) ($subscription['id'] ?? '');
        if ($surecart_subscription_id === '') {
            return;
        }

        $row = [
            'clinic_id' => $clinic_id,
            'owner_user_id' => $owner_user_id ?: null,
            'plan_entitlement_id' => absint($meta['plan_entitlement_id'] ?? 0) ?: null,
            'surecart_customer_id' => (string) ($subscription['customer_id'] ?? ''),
            'surecart_subscription_id' => $surecart_subscription_id,
            'surecart_product_id' => (string) ($subscription['product_id'] ?? ''),
            'surecart_price_id' => (string) ($subscription['price_id'] ?? ''),
            'status' => (string) ($subscription['status'] ?? 'pending'),
            'started_at_gmt' => self::to_mysql_datetime($subscription['created_at'] ?? null),
            'current_period_end_gmt' => self::to_mysql_datetime($subscription['current_period_end'] ?? null),
            'cancel_at_period_end' => ! empty($subscription['cancel_at_period_end']) ? 1 : 0,
            'last_event_type' => $event_type,
            'updated_at_gmt' => current_time('mysql', true),
        ];

        $existing = $wpdb->get_var(
            $wpdb->prepare(
                "SELECT id FROM {$table} WHERE surecart_subscription_id = %s",
                $surecart_subscription_id
            )
        );

        if ($existing) {
            $wpdb->update($table, $row, ['id' => (int) $existing]);
            return;
        }

        $row['created_at_gmt'] = current_time('mysql', true);
        $wpdb->insert($table, $row);
    }

    private static function to_mysql_datetime(mixed $value): ?string
    {
        if (! $value) {
            return null;
        }

        $timestamp = strtotime((string) $value);
        return $timestamp ? gmdate('Y-m-d H:i:s', $timestamp) : null;
    }
}
