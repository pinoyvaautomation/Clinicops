<?php

declare(strict_types=1);

namespace ClinicOps\Core\Integrations;

if (! defined('ABSPATH')) {
    exit;
}

final class SureCart_Hooks
{
    public static function init(): void
    {
        add_action('surecart/subscription_renewed', [self::class, 'handle_subscription_renewed'], 10, 1);
    }

    /**
     * This hook name is documented by SureCart and gives us a low-latency sync point
     * when the plugin is installed locally. Broader lifecycle coverage should still
     * come from webhooks for auditability and recovery.
     *
     * @param mixed $subscription
     */
    public static function handle_subscription_renewed($subscription): void
    {
        if (! is_array($subscription)) {
            return;
        }

        do_action('clinicops_core_surecart_subscription_renewed', $subscription);
    }
}
