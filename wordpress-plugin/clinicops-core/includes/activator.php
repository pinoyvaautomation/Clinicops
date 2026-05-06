<?php

declare(strict_types=1);

namespace ClinicOps\Core;

if (! defined('ABSPATH')) {
    exit;
}

final class Activator
{
    public static function activate(): void
    {
        Installer::install();
        Roles_Capabilities::register();
        Cron\Reminders::schedule();
        update_option('clinicops_core_version', CLINICOPS_CORE_VERSION, false);
        flush_rewrite_rules(false);
    }
}
