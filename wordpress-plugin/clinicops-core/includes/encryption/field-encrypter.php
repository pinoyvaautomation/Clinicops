<?php

declare(strict_types=1);

namespace ClinicOps\Core\Encryption;

if (! defined('ABSPATH')) {
    exit;
}

final class Field_Encrypter
{
    public static function encrypt(?string $value): ?string
    {
        if ($value === null || $value === '') {
            return $value;
        }

        $key = self::key();
        $iv = random_bytes(16);
        $cipher = openssl_encrypt($value, 'aes-256-cbc', $key, OPENSSL_RAW_DATA, $iv);
        if ($cipher === false) {
            return null;
        }

        return base64_encode($iv . $cipher);
    }

    public static function decrypt(?string $value): ?string
    {
        if ($value === null || $value === '') {
            return $value;
        }

        $decoded = base64_decode($value, true);
        if ($decoded === false || strlen($decoded) <= 16) {
            return null;
        }

        $iv = substr($decoded, 0, 16);
        $ciphertext = substr($decoded, 16);
        $plain = openssl_decrypt($ciphertext, 'aes-256-cbc', self::key(), OPENSSL_RAW_DATA, $iv);

        return $plain === false ? null : $plain;
    }

    private static function key(): string
    {
        if (defined('CLINICOPS_CORE_ENCRYPTION_KEY') && CLINICOPS_CORE_ENCRYPTION_KEY) {
            return hash('sha256', (string) CLINICOPS_CORE_ENCRYPTION_KEY, true);
        }

        return hash('sha256', wp_salt('auth'), true);
    }
}
