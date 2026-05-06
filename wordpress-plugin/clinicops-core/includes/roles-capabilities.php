<?php

declare(strict_types=1);

namespace ClinicOps\Core;

if (! defined('ABSPATH')) {
    exit;
}

final class Roles_Capabilities
{
    /**
     * @return array<int, string>
     */
    public static function capabilities(): array
    {
        return [
            'clinicops_access_portal',
            'clinicops_manage_clinic',
            'clinicops_manage_staff',
            'clinicops_manage_patients',
            'clinicops_view_patients',
            'clinicops_manage_appointments',
            'clinicops_view_appointments',
            'clinicops_manage_waitlist',
            'clinicops_use_messaging',
            'clinicops_reply_messages',
            'clinicops_manage_billing',
            'clinicops_manage_support',
            'clinicops_manage_security',
        ];
    }

    public static function register(): void
    {
        $map = [
            'clinic_admin' => [
                'label' => 'Clinic Admin',
                'caps' => self::capabilities(),
            ],
            'clinic_doctor' => [
                'label' => 'Clinic Doctor',
                'caps' => [
                    'read',
                    'clinicops_access_portal',
                    'clinicops_view_patients',
                    'clinicops_view_appointments',
                    'clinicops_manage_appointments',
                    'clinicops_use_messaging',
                    'clinicops_reply_messages',
                ],
            ],
            'clinic_nurse' => [
                'label' => 'Clinic Nurse',
                'caps' => [
                    'read',
                    'clinicops_access_portal',
                    'clinicops_view_patients',
                    'clinicops_view_appointments',
                    'clinicops_manage_appointments',
                    'clinicops_use_messaging',
                ],
            ],
            'clinic_frontdesk' => [
                'label' => 'Clinic Front Desk',
                'caps' => [
                    'read',
                    'clinicops_access_portal',
                    'clinicops_view_patients',
                    'clinicops_view_appointments',
                    'clinicops_manage_appointments',
                    'clinicops_manage_waitlist',
                    'clinicops_use_messaging',
                    'clinicops_reply_messages',
                ],
            ],
            'clinic_patient' => [
                'label' => 'Clinic Patient',
                'caps' => [
                    'read',
                    'clinicops_access_portal',
                ],
            ],
        ];

        foreach ($map as $role_key => $role_config) {
            add_role($role_key, $role_config['label'], ['read' => true]);
            $role = get_role($role_key);
            if (! $role) {
                continue;
            }

            foreach ($role_config['caps'] as $cap) {
                $role->add_cap($cap);
            }
        }

        $admin = get_role('administrator');
        if ($admin) {
            foreach (self::capabilities() as $cap) {
                $admin->add_cap($cap);
            }
        }
    }
}
