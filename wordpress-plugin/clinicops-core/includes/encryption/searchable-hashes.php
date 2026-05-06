<?php

declare(strict_types=1);

namespace ClinicOps\Core\Encryption;

if (! defined('ABSPATH')) {
    exit;
}

final class Searchable_Hashes
{
    public static function email(?string $value): string
    {
        return self::hash(self::normalize_email($value));
    }

    public static function phone(?string $value): string
    {
        return self::hash(self::normalize_phone($value));
    }

    public static function hash(string $value): string
    {
        return hash_hmac('sha256', $value, wp_salt('secure_auth'));
    }

    private static function normalize_email(?string $value): string
    {
        return strtolower(trim((string) $value));
    }

    private static function normalize_phone(?string $value): string
    {
        return preg_replace('/\D+/', '', (string) $value) ?: '';
    }
}
