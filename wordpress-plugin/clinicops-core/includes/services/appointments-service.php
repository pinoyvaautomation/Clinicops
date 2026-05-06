<?php

declare(strict_types=1);

namespace ClinicOps\Core\Services;

use WP_Error;

if (! defined('ABSPATH')) {
    exit;
}

final class Appointments_Service
{
    /**
     * @param array<string, mixed> $data
     * @return array<string, mixed>|WP_Error
     */
    public static function validate_request(array $data)
    {
        $required = ['clinic_id', 'staff_id', 'patient_id', 'start_at_gmt', 'end_at_gmt'];
        foreach ($required as $field) {
            if (empty($data[$field])) {
                return new WP_Error('clinicops_missing_field', sprintf('%s is required.', $field), ['status' => 400]);
            }
        }

        $start = strtotime((string) $data['start_at_gmt']);
        $end = strtotime((string) $data['end_at_gmt']);
        if (! $start || ! $end || $start >= $end) {
            return new WP_Error('clinicops_invalid_times', 'Appointment end time must be later than start time.', ['status' => 400]);
        }

        return [
            'clinic_id' => absint($data['clinic_id']),
            'staff_id' => absint($data['staff_id']),
            'patient_id' => absint($data['patient_id']),
            'start_at_gmt' => gmdate('Y-m-d H:i:s', $start),
            'end_at_gmt' => gmdate('Y-m-d H:i:s', $end),
        ];
    }
}
