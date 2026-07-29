# Native MySQL database

The web server uses a native MySQL 8 installation exclusively. There is no
SQLite runtime, database file, Docker container, or container volume.

The schema contains registered vehicles, access events, dashboard users, audit
logs, system status, settings, indexes, foreign keys, constraints, and the
seven-day activity view. It is safe to apply `schema.sql` more than once.

Three additive `account_service_*` tables reserve a minimal local entitlement
cache, synchronization cursor, and sync audit trail for the future homeowner
mobile-account service. They are dormant and are not used by the current gate
authorization query.

Install and start MySQL directly on the PC, create the `plate_access_control`
database and restricted `gatekeeper` account, then configure the matching
credentials in the private `.env` file.

Apply or update the schema with:

```bash
./database/init_database.sh
```

The Flask application also reapplies the idempotent schema when it starts.
MySQL stores structured records, while captured JPEG crops remain in `Output`
and their relative paths are stored in `access_events.image_path`.
