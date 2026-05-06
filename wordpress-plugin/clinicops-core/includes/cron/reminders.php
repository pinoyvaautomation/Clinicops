<?php

declare(strict_types=1);

namespace ClinicOps\Core\Cron;

if (! defined('ABSPATH')) {
    exit;
}

final class Reminders
{
    public static function init(): void
    {
        add_filter('cron_schedules', [self::class, 'register_schedule']);
        add_action('clinicops_core_send_reminders', [self::class, 'handle']);
    }

    /**
     * @param array<string, array<string, mixed>> $schedules
     * @return array<string, array<string, mixed>>
     */
    public static function register_schedule(array $schedules): array
    {
        $schedules['clinicops_ten_minutes'] = [
            'interval' => 600,
            'display' => 'Every 10 Minutes',
        ];

        return $schedules;
    }

    public static function schedule(): void
    {
        if (! wp_next_scheduled('clinicops_core_send_reminders')) {
            wp_schedule_event(time() + 60, 'clinicops_ten_minutes', 'clinicops_core_send_reminders');
        }
    }

    public static function unschedule(): void
    {
        $timestamp = wp_next_scheduled('clinicops_core_send_reminders');
        if ($timestamp) {
            wp_unschedule_event($timestamp, 'clinicops_core_send_reminders');
        }
    }

    public static function handle(): void
    {
        /**
         * Hook reminder delivery into a service later so the cron contract is stable now.
         */
        do_action('clinicops_core_reminders_run');
    }
}
