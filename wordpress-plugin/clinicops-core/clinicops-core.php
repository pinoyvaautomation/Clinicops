<?php
/**
 * Plugin Name: ClinicOps Core
 * Plugin URI: https://clinicops.local
 * Description: ClinicOps domain logic, custom tables, REST API bootstrap, and SureCart integration for WordPress.
 * Version: 0.1.0
 * Requires at least: 6.5
 * Requires PHP: 8.1
 * Author: ClinicOps
 * Text Domain: clinicops-core
 */

declare(strict_types=1);

if (! defined('ABSPATH')) {
    exit;
}

define('CLINICOPS_CORE_VERSION', '0.1.0');
define('CLINICOPS_CORE_FILE', __FILE__);
define('CLINICOPS_CORE_PATH', plugin_dir_path(__FILE__));
define('CLINICOPS_CORE_URL', plugin_dir_url(__FILE__));

require_once CLINICOPS_CORE_PATH . 'includes/bootstrap.php';

register_activation_hook(CLINICOPS_CORE_FILE, [\ClinicOps\Core\Activator::class, 'activate']);
register_deactivation_hook(CLINICOPS_CORE_FILE, [\ClinicOps\Core\Deactivator::class, 'deactivate']);

add_action('plugins_loaded', [\ClinicOps\Core\Bootstrap::class, 'init']);
