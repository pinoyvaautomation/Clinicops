<?php

declare(strict_types=1);

namespace ClinicOps\Core\Admin;

if (! defined('ABSPATH')) {
    exit;
}

final class Settings_Page
{
    public static function register(): void
    {
        register_setting('clinicops_core', 'clinicops_core_settings', [
            'type' => 'array',
            'sanitize_callback' => [self::class, 'sanitize'],
            'default' => [
                'default_timezone' => 'UTC',
                'reminder_window_minutes' => 1440,
            ],
        ]);

        add_settings_section(
            'clinicops_core_general',
            'ClinicOps Core Settings',
            '__return_false',
            'clinicops-core'
        );

        add_settings_field(
            'default_timezone',
            'Default timezone',
            [self::class, 'render_default_timezone'],
            'clinicops-core',
            'clinicops_core_general'
        );

        add_settings_field(
            'reminder_window_minutes',
            'Reminder window (minutes)',
            [self::class, 'render_reminder_window'],
            'clinicops-core',
            'clinicops_core_general'
        );
    }

    /**
     * @param array<string, mixed> $input
     * @return array<string, mixed>
     */
    public static function sanitize(array $input): array
    {
        return [
            'default_timezone' => sanitize_text_field((string) ($input['default_timezone'] ?? 'UTC')),
            'reminder_window_minutes' => max(10, absint($input['reminder_window_minutes'] ?? 1440)),
        ];
    }

    public static function render(): void
    {
        if (! current_user_can('clinicops_manage_clinic')) {
            wp_die(esc_html__('You do not have permission to access ClinicOps settings.', 'clinicops-core'));
        }

        ?>
        <div class="wrap">
            <h1>ClinicOps Core</h1>
            <p>Schema installer, REST bootstrap, and SureCart integration scaffold for the ClinicOps migration.</p>
            <form method="post" action="options.php">
                <?php
                settings_fields('clinicops_core');
                do_settings_sections('clinicops-core');
                submit_button('Save Settings');
                ?>
            </form>
        </div>
        <?php
    }

    public static function render_default_timezone(): void
    {
        $settings = get_option('clinicops_core_settings', []);
        ?>
        <input
            type="text"
            name="clinicops_core_settings[default_timezone]"
            value="<?php echo esc_attr((string) ($settings['default_timezone'] ?? 'UTC')); ?>"
            class="regular-text"
        />
        <?php
    }

    public static function render_reminder_window(): void
    {
        $settings = get_option('clinicops_core_settings', []);
        ?>
        <input
            type="number"
            name="clinicops_core_settings[reminder_window_minutes]"
            value="<?php echo esc_attr((string) ($settings['reminder_window_minutes'] ?? 1440)); ?>"
            min="10"
            step="10"
            class="small-text"
        />
        <?php
    }
}
