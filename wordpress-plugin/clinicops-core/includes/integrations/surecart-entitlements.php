<?php

declare(strict_types=1);

namespace ClinicOps\Core\Integrations;

use wpdb;

if (! defined('ABSPATH')) {
    exit;
}

final class SureCart_Entitlements
{
    /**
     * @return array<string, mixed>|null
     */
    public static function current_for_clinic(int $clinic_id): ?array
    {
        global $wpdb;

        $subscriptions_table = $wpdb->prefix . 'clinicops_subscriptions';
        $plans_table = $wpdb->prefix . 'clinicops_plan_entitlements';

        $sql = $wpdb->prepare(
            "SELECT p.* FROM {$subscriptions_table} s
             LEFT JOIN {$plans_table} p ON p.id = s.plan_entitlement_id
             WHERE s.clinic_id = %d
               AND s.status IN ('active', 'trialing')
             ORDER BY s.updated_at_gmt DESC
             LIMIT 1",
            $clinic_id
        );

        $row = $wpdb->get_row($sql, ARRAY_A);
        return is_array($row) ? $row : null;
    }
}
