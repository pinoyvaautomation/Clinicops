<?php

declare(strict_types=1);

namespace ClinicOps\Core;

if (! defined('ABSPATH')) {
    exit;
}

final class Deactivator
{
    public static function deactivate(): void
    {
        Cron\Reminders::unschedule();
        flush_rewrite_rules(false);
    }
}
