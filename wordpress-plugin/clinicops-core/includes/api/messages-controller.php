<?php

declare(strict_types=1);

namespace ClinicOps\Core\Api;

use WP_REST_Request;
use WP_REST_Response;

if (! defined('ABSPATH')) {
    exit;
}

final class Messages_Controller
{
    public static function register_routes(): void
    {
        register_rest_route('clinicops/v1', '/messages/threads', [
            'methods' => 'GET',
            'callback' => [self::class, 'threads'],
            'permission_callback' => static fn (): bool => current_user_can('clinicops_use_messaging'),
        ]);
    }

    public static function threads(WP_REST_Request $request): WP_REST_Response
    {
        unset($request);

        return new WP_REST_Response([
            'items' => [],
            'message' => 'Messaging thread endpoint scaffolded.',
        ]);
    }
}
