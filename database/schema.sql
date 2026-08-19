CREATE TABLE IF NOT EXISTS vehicles (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    plate_number VARCHAR(20) NOT NULL,
    owner_name VARCHAR(160) NOT NULL,
    vehicle_type VARCHAR(80) NULL,
    make VARCHAR(80) NULL,
    model VARCHAR(80) NULL,
    color VARCHAR(80) NULL,
    contact_number VARCHAR(80) NULL,
    email VARCHAR(254) NULL,
    registration_expires_on DATE NULL,
    photo_path VARCHAR(500) NULL,
    notes TEXT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_vehicles_plate_number (plate_number),
    KEY idx_vehicles_active_plate (is_active, plate_number),
    CONSTRAINT chk_vehicles_plate CHECK (plate_number REGEXP '^[A-Z0-9]{1,20}$'),
    CONSTRAINT chk_vehicles_active CHECK (is_active IN (0, 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS rfid_stickers (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    vehicle_id BIGINT UNSIGNED NOT NULL,
    sticker_value VARCHAR(64) NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_rfid_stickers_value (sticker_value),
    KEY idx_rfid_stickers_vehicle (vehicle_id, is_active),
    CONSTRAINT fk_rfid_stickers_vehicle FOREIGN KEY (vehicle_id)
        REFERENCES vehicles(id) ON DELETE CASCADE,
    CONSTRAINT chk_rfid_stickers_value CHECK (
        sticker_value REGEXP '^[A-Z0-9]{4,64}$'
    ),
    CONSTRAINT chk_rfid_stickers_active CHECK (is_active IN (0, 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS controllers (
    controller_uid VARCHAR(64) NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    controller_type ENUM('rfid', 'plate') NOT NULL,
    camera_state VARCHAR(40) NOT NULL DEFAULT 'unknown',
    detector_state VARCHAR(40) NOT NULL DEFAULT 'unknown',
    gate_state VARCHAR(40) NOT NULL DEFAULT 'closed',
    camera_connected TINYINT(1) NOT NULL DEFAULT 0,
    rfid_connected TINYINT(1) NOT NULL DEFAULT 0,
    loop_active TINYINT(1) NOT NULL DEFAULT 0,
    ir_blocked TINYINT(1) NOT NULL DEFAULT 0,
    barrier_open TINYINT(1) NOT NULL DEFAULT 0,
    traffic_green TINYINT(1) NOT NULL DEFAULT 0,
    plate_unrecognized TINYINT(1) NOT NULL DEFAULT 0,
    last_plate VARCHAR(20) NULL,
    last_rfid VARCHAR(64) NULL,
    controller_seen_at TIMESTAMP NULL,
    last_heartbeat TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (controller_uid),
    KEY idx_controllers_seen (controller_seen_at DESC),
    CONSTRAINT chk_controllers_uid CHECK (
        controller_uid REGEXP '^[A-Za-z0-9._:-]{1,64}$'
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS access_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    controller_uid VARCHAR(64) NULL,
    vehicle_id BIGINT UNSIGNED NULL,
    plate_number VARCHAR(20) NOT NULL,
    rfid_number VARCHAR(64) NULL,
    rfid_required TINYINT(1) NOT NULL DEFAULT 0,
    rfid_authorized TINYINT(1) NOT NULL DEFAULT 0,
    direction ENUM('entry', 'exit') NOT NULL DEFAULT 'entry',
    decision ENUM('authorized', 'denied', 'unreadable', 'manual') NOT NULL,
    gate_action ENUM('opened', 'kept_closed', 'not_requested', 'error') NOT NULL DEFAULT 'kept_closed',
    detector_confidence DECIMAL(6,5) NULL,
    ocr_confidence DECIMAL(6,5) NULL,
    image_path VARCHAR(500) NULL,
    raw_image_path VARCHAR(500) NULL,
    annotated_image_path VARCHAR(500) NULL,
    notes TEXT NULL,
    detected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_access_events_detected_at (detected_at DESC),
    KEY idx_access_events_plate (plate_number, detected_at DESC),
    KEY idx_access_events_rfid (rfid_number, detected_at DESC),
    KEY idx_access_events_decision (decision, detected_at DESC),
    KEY idx_access_events_controller (controller_uid, detected_at DESC),
    CONSTRAINT fk_access_events_vehicle FOREIGN KEY (vehicle_id)
        REFERENCES vehicles(id) ON DELETE SET NULL,
    CONSTRAINT chk_access_detector_confidence CHECK (
        detector_confidence IS NULL OR detector_confidence BETWEEN 0.0 AND 1.0
    ),
    CONSTRAINT chk_access_ocr_confidence CHECK (
        ocr_confidence IS NULL OR ocr_confidence BETWEEN 0.0 AND 1.0
    ),
    CONSTRAINT chk_access_rfid_required CHECK (rfid_required IN (0, 1)),
    CONSTRAINT chk_access_rfid_authorized CHECK (rfid_authorized IN (0, 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS users (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    username VARCHAR(100) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role ENUM('administrator', 'operator', 'viewer') NOT NULL DEFAULT 'operator',
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_username (username),
    CONSTRAINT chk_users_active CHECK (is_active IN (0, 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS audit_log (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id BIGINT UNSIGNED NULL,
    action VARCHAR(100) NOT NULL,
    entity_type VARCHAR(100) NULL,
    entity_id BIGINT UNSIGNED NULL,
    details TEXT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_audit_log_created_at (created_at DESC),
    CONSTRAINT fk_audit_log_user FOREIGN KEY (user_id)
        REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS reader_commands (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    controller_uid VARCHAR(64) NULL,
    command_type ENUM(
        'capture', 'barrier_open', 'barrier_close',
        'traffic_red', 'traffic_green', 'rfid_serial'
    ) NOT NULL DEFAULT 'capture',
    status ENUM('pending', 'active', 'completed', 'failed') NOT NULL DEFAULT 'pending',
    requested_by BIGINT UNSIGNED NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP NULL,
    completed_at TIMESTAMP NULL,
    result_message VARCHAR(500) NULL,
    serial_tx_hex VARCHAR(1024) NULL,
    serial_baud INT UNSIGNED NULL,
    serial_data_bits TINYINT UNSIGNED NULL,
    serial_parity CHAR(1) NULL,
    serial_stop_bits TINYINT UNSIGNED NULL,
    serial_timeout_ms INT UNSIGNED NULL,
    response_data TEXT NULL,
    frames_ms INT UNSIGNED NULL,
    yolo_ms INT UNSIGNED NULL,
    ocr_ms INT UNSIGNED NULL,
    server_ms INT UNSIGNED NULL,
    total_ms INT UNSIGNED NULL,
    PRIMARY KEY (id),
    KEY idx_reader_commands_status_created (status, created_at),
    KEY idx_reader_commands_controller_status (controller_uid, status, created_at),
    CONSTRAINT fk_reader_commands_user FOREIGN KEY (requested_by)
        REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS system_status (
    id TINYINT UNSIGNED NOT NULL,
    controller_type VARCHAR(20) NOT NULL DEFAULT 'plate',
    camera_state VARCHAR(40) NOT NULL DEFAULT 'unknown',
    detector_state VARCHAR(40) NOT NULL DEFAULT 'unknown',
    gate_state VARCHAR(40) NOT NULL DEFAULT 'closed',
    camera_connected TINYINT(1) NOT NULL DEFAULT 0,
    rfid_connected TINYINT(1) NOT NULL DEFAULT 0,
    loop_active TINYINT(1) NOT NULL DEFAULT 0,
    ir_blocked TINYINT(1) NOT NULL DEFAULT 0,
    barrier_open TINYINT(1) NOT NULL DEFAULT 0,
    traffic_green TINYINT(1) NOT NULL DEFAULT 0,
    plate_unrecognized TINYINT(1) NOT NULL DEFAULT 0,
    controller_seen_at TIMESTAMP NULL,
    last_plate VARCHAR(20) NULL,
    last_rfid VARCHAR(64) NULL,
    last_heartbeat TIMESTAMP NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    CONSTRAINT chk_system_status_singleton CHECK (id = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS settings (
    `key` VARCHAR(100) NOT NULL,
    `value` VARCHAR(500) NOT NULL,
    description VARCHAR(500) NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Dormant local cache for the future homeowner account service. These tables
-- are additive and are not consulted by the existing reader authorization
-- query until the integration is explicitly implemented and enabled.
CREATE TABLE IF NOT EXISTS account_service_entitlements (
    vehicle_id BIGINT UNSIGNED NOT NULL,
    remote_vehicle_id VARCHAR(100) NOT NULL,
    remote_household_id VARCHAR(100) NOT NULL,
    entitlement_status ENUM(
        'unconfigured', 'active', 'past_due', 'expired', 'suspended'
    ) NOT NULL DEFAULT 'unconfigured',
    paid_through DATE NULL,
    grace_until DATE NULL,
    source_revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
    last_synced_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (vehicle_id),
    UNIQUE KEY uq_account_entitlements_remote_vehicle (remote_vehicle_id),
    KEY idx_account_entitlements_household (remote_household_id),
    KEY idx_account_entitlements_status_expiry (
        entitlement_status, paid_through, grace_until
    ),
    CONSTRAINT fk_account_entitlements_vehicle FOREIGN KEY (vehicle_id)
        REFERENCES vehicles(id) ON DELETE CASCADE,
    CONSTRAINT chk_account_entitlements_dates CHECK (
        grace_until IS NULL OR paid_through IS NULL OR grace_until >= paid_through
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS account_service_sync_state (
    id TINYINT UNSIGNED NOT NULL,
    site_id VARCHAR(100) NULL,
    remote_cursor VARCHAR(255) NULL,
    last_attempt_at TIMESTAMP NULL,
    last_success_at TIMESTAMP NULL,
    last_error VARCHAR(1000) NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    CONSTRAINT chk_account_sync_state_singleton CHECK (id = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS account_service_sync_audit (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    sync_direction ENUM('pull', 'push') NOT NULL,
    sync_status ENUM('started', 'succeeded', 'failed', 'ignored') NOT NULL,
    source_revision BIGINT UNSIGNED NULL,
    records_received INT UNSIGNED NOT NULL DEFAULT 0,
    records_applied INT UNSIGNED NOT NULL DEFAULT 0,
    details VARCHAR(1000) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_account_sync_audit_created (created_at DESC),
    KEY idx_account_sync_audit_status (sync_status, created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE OR REPLACE VIEW daily_access_summary AS
SELECT
    DATE_FORMAT(detected_at, '%Y-%m-%d') AS event_date,
    COUNT(*) AS total_events,
    SUM(CASE WHEN decision = 'authorized' THEN 1 ELSE 0 END) AS authorized_count,
    SUM(CASE WHEN decision = 'denied' THEN 1 ELSE 0 END) AS denied_count,
    SUM(CASE WHEN gate_action = 'opened' THEN 1 ELSE 0 END) AS gates_opened
FROM access_events
GROUP BY DATE_FORMAT(detected_at, '%Y-%m-%d');

INSERT IGNORE INTO system_status (id) VALUES (1);
INSERT IGNORE INTO account_service_sync_state (id) VALUES (1);

INSERT IGNORE INTO settings (`key`, `value`, description) VALUES
    ('gate_open_seconds', '5', 'How long the gate-open signal remains active.'),
    ('duplicate_event_seconds', '30', 'Suppress repeated events for the same plate.'),
    ('event_image_retention_days', '90', 'Days to keep event snapshot files.'),
    ('unknown_plate_action', 'deny', 'Action for an unregistered or inactive plate.');
