<?php

declare(strict_types=1);

namespace ClinicOps\Core\Api;

use ClinicOps\Core\Integrations\SureCart_Webhooks;
use WP_REST_Request;
use WP_REST_Response;

if (! defined('ABSPATH')) {
    exit;
}

final class Rest_Routes
{
    public static function init(): void
    {
        add_action('rest_api_init', [self::class, 'register']);
    }

    public static function register(): void
    {
        register_rest_route('clinicops/v1', '/health', [
            'methods' => 'GET',
            'callback' => [self::class, 'health'],
            'permission_callback' => static fn (): bool => current_user_can('clinicops_access_portal'),
        ]);

        register_rest_route('clinicops/v1', '/surecart/webhook', [
            'methods' => 'POST',
            'callback' => [SureCart_Webhooks::class, 'handle'],
            'permission_callback' => '__return_true',
        ]);

        Appointments_Controller::register_routes();
        Patients_Controller::register_routes();
        Messages_Controller::register_routes();
        Search_Controller::register_routes();
    }

    public static function health(WP_REST_Request $request): WP_REST_Response
    {
        unset($request);

        return new WP_REST_Response([
            'plugin' => 'clinicops-core',
            'version' => CLINICOPS_CORE_VERSION,
            'status' => 'ok',
        ]);
    }
}
