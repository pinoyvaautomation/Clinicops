<?php

declare(strict_types=1);

namespace ClinicOps\Core\Api;

use ClinicOps\Core\Services\Appointments_Service;
use WP_REST_Request;
use WP_REST_Response;

if (! defined('ABSPATH')) {
    exit;
}

final class Appointments_Controller
{
    public static function register_routes(): void
    {
        register_rest_route('clinicops/v1', '/appointments', [
            [
                'methods' => 'GET',
                'callback' => [self::class, 'index'],
                'permission_callback' => static fn (): bool => current_user_can('clinicops_view_appointments'),
            ],
            [
                'methods' => 'POST',
                'callback' => [self::class, 'create'],
                'permission_callback' => static fn (): bool => current_user_can('clinicops_manage_appointments'),
            ],
        ]);
    }

    public static function index(WP_REST_Request $request): WP_REST_Response
    {
        unset($request);

        return new WP_REST_Response([
            'items' => [],
            'message' => 'Appointments endpoint scaffolded. Connect a repository/service next.',
        ]);
    }

    public static function create(WP_REST_Request $request)
    {
        $params = $request->get_json_params();

        $validation = Appointments_Service::validate_request(is_array($params) ? $params : []);
        if (is_wp_error($validation)) {
            return $validation;
        }

        return new WP_REST_Response([
            'message' => 'Appointment create pipeline scaffolded.',
            'validated' => $validation,
        ], 202);
    }
}
