<?php

declare(strict_types=1);

namespace ClinicOps\Core\Api;

use WP_REST_Request;
use WP_REST_Response;

if (! defined('ABSPATH')) {
    exit;
}

final class Patients_Controller
{
    public static function register_routes(): void
    {
        register_rest_route('clinicops/v1', '/patients', [
            'methods' => 'GET',
            'callback' => [self::class, 'index'],
            'permission_callback' => static fn (): bool => current_user_can('clinicops_view_patients'),
        ]);
    }

    public static function index(WP_REST_Request $request): WP_REST_Response
    {
        unset($request);

        return new WP_REST_Response([
            'items' => [],
            'message' => 'Patients endpoint scaffolded. Add encrypted search and repository queries next.',
        ]);
    }
}
