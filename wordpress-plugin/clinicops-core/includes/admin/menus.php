<?php

declare(strict_types=1);

namespace ClinicOps\Core\Admin;

if (! defined('ABSPATH')) {
    exit;
}

final class Menus
{
    public static function init(): void
    {
        add_action('admin_menu', [self::class, 'register']);
        add_action('admin_init', [Settings_Page::class, 'register']);
    }

    public static function register(): void
    {
        add_menu_page(
            'ClinicOps',
            'ClinicOps',
            'clinicops_manage_clinic',
            'clinicops-core',
            [Settings_Page::class, 'render'],
            'dashicons-calendar-alt',
            56
        );

        add_submenu_page(
            'clinicops-core',
            'ClinicOps Settings',
            'Settings',
            'clinicops_manage_clinic',
            'clinicops-core',
            [Settings_Page::class, 'render']
        );
    }
}
