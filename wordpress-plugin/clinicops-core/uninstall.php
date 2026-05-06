<?php
/**
 * Uninstall handler for ClinicOps Core.
 *
 * Deliberately keeps data in place by default. If a site owner wants destructive
 * cleanup later, add an explicit admin flow instead of deleting tables on uninstall.
 */

declare(strict_types=1);

if (! defined('WP_UNINSTALL_PLUGIN')) {
    exit;
}

$timestamp = wp_next_scheduled('clinicops_core_send_reminders');
if ($timestamp) {
    wp_unschedule_event($timestamp, 'clinicops_core_send_reminders');
}
