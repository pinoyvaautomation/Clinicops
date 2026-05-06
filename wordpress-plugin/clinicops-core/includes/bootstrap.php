<?php

declare(strict_types=1);

namespace ClinicOps\Core;

if (! defined('ABSPATH')) {
    exit;
}

require_once CLINICOPS_CORE_PATH . 'includes/activator.php';
require_once CLINICOPS_CORE_PATH . 'includes/deactivator.php';
require_once CLINICOPS_CORE_PATH . 'includes/installer.php';
require_once CLINICOPS_CORE_PATH . 'includes/roles-capabilities.php';
require_once CLINICOPS_CORE_PATH . 'includes/admin/menus.php';
require_once CLINICOPS_CORE_PATH . 'includes/admin/settings-page.php';
require_once CLINICOPS_CORE_PATH . 'includes/api/rest-routes.php';
require_once CLINICOPS_CORE_PATH . 'includes/api/appointments-controller.php';
require_once CLINICOPS_CORE_PATH . 'includes/api/patients-controller.php';
require_once CLINICOPS_CORE_PATH . 'includes/api/messages-controller.php';
require_once CLINICOPS_CORE_PATH . 'includes/api/search-controller.php';
require_once CLINICOPS_CORE_PATH . 'includes/cron/reminders.php';
require_once CLINICOPS_CORE_PATH . 'includes/encryption/field-encrypter.php';
require_once CLINICOPS_CORE_PATH . 'includes/encryption/searchable-hashes.php';
require_once CLINICOPS_CORE_PATH . 'includes/integrations/surecart-entitlements.php';
require_once CLINICOPS_CORE_PATH . 'includes/services/appointments-service.php';
require_once CLINICOPS_CORE_PATH . 'includes/services/subscriptions-service.php';
require_once CLINICOPS_CORE_PATH . 'includes/integrations/surecart-hooks.php';
require_once CLINICOPS_CORE_PATH . 'includes/integrations/surecart-webhooks.php';

final class Bootstrap
{
    private static bool $booted = false;

    public static function init(): void
    {
        if (self::$booted) {
            return;
        }

        self::$booted = true;

        Roles_Capabilities::register();
        Admin\Menus::init();
        Api\Rest_Routes::init();
        Cron\Reminders::init();
        Integrations\SureCart_Hooks::init();
        Integrations\SureCart_Webhooks::init();
    }
}
