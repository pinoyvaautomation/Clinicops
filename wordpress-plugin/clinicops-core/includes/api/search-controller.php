<?php

declare(strict_types=1);

namespace ClinicOps\Core\Api;

use WP_REST_Request;
use WP_REST_Response;

if (! defined('ABSPATH')) {
    exit;
}

final class Search_Controller
{
    public static function register_routes(): void
    {
        register_rest_route('clinicops/v1', '/search', [
            'methods' => 'GET',
            'callback' => [self::class, 'search'],
            'permission_callback' => static fn (): bool => current_user_can('clinicops_view_patients'),
        ]);
    }

    public static function search(WP_REST_Request $request): WP_REST_Response
    {
        return new WP_REST_Response([
            'query' => sanitize_text_field((string) $request->get_param('q')),
            'items' => [],
            'message' => 'Global portal search endpoint scaffolded. Wire hashed email/phone and confirmation-code search next.',
        ]);
    }
}
