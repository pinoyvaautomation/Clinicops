# ClinicOps Core WordPress Plugin

This package is the WordPress migration scaffold for ClinicOps.

It provides:

- plugin bootstrap and activation wiring
- custom-table installer for clinic domain data
- WordPress roles and capabilities for clinic access
- REST API bootstrap with placeholder controllers
- SureCart webhook/event sync stubs
- encryption and hashing helpers for sensitive fields
- reminder cron registration

This scaffold is intentionally opinionated:

- clinical domain data belongs in custom tables
- billing stays in SureCart
- the plugin keeps only local entitlement and subscription projection tables

Reference:

- [WORDPRESS_SURECART_SCHEMA.md](../../WORDPRESS_SURECART_SCHEMA.md)
