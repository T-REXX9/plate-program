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

CREATE TABLE IF NOT EXISTS access_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    vehicle_id BIGINT UNSIGNED NULL,
    plate_number VARCHAR(20) NOT NULL,
    direction ENUM('entry', 'exit') NOT NULL DEFAULT 'entry',
    decision ENUM('authorized', 'denied', 'unreadable', 'manual') NOT NULL,
    gate_action ENUM('opened', 'kept_closed', 'not_requested', 'error') NOT NULL DEFAULT 'kept_closed',
    detector_confidence DECIMAL(6,5) NULL,
    ocr_confidence DECIMAL(6,5) NULL,
    image_path VARCHAR(500) NULL,
    notes TEXT NULL,
    detected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_access_events_detected_at (detected_at DESC),
    KEY idx_access_events_plate (plate_number, detected_at DESC),
    KEY idx_access_events_decision (decision, detected_at DESC),
    CONSTRAINT fk_access_events_vehicle FOREIGN KEY (vehicle_id)
        REFERENCES vehicles(id) ON DELETE SET NULL,
    CONSTRAINT chk_access_detector_confidence CHECK (
        detector_confidence IS NULL OR detector_confidence BETWEEN 0.0 AND 1.0
    ),
    CONSTRAINT chk_access_ocr_confidence CHECK (
        ocr_confidence IS NULL OR ocr_confidence BETWEEN 0.0 AND 1.0
    )
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
    command_type ENUM('capture') NOT NULL DEFAULT 'capture',
    status ENUM('pending', 'active', 'completed', 'failed') NOT NULL DEFAULT 'pending',
    requested_by BIGINT UNSIGNED NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP NULL,
    completed_at TIMESTAMP NULL,
    result_message VARCHAR(500) NULL,
    frames_ms INT UNSIGNED NULL,
    yolo_ms INT UNSIGNED NULL,
    ocr_ms INT UNSIGNED NULL,
    server_ms INT UNSIGNED NULL,
    total_ms INT UNSIGNED NULL,
    PRIMARY KEY (id),
    KEY idx_reader_commands_status_created (status, created_at),
    CONSTRAINT fk_reader_commands_user FOREIGN KEY (requested_by)
        REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS system_status (
    id TINYINT UNSIGNED NOT NULL,
    camera_state VARCHAR(40) NOT NULL DEFAULT 'unknown',
    detector_state VARCHAR(40) NOT NULL DEFAULT 'unknown',
    gate_state VARCHAR(40) NOT NULL DEFAULT 'closed',
    last_plate VARCHAR(20) NULL,
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

INSERT IGNORE INTO settings (`key`, `value`, description) VALUES
    ('gate_open_seconds', '5', 'How long the gate-open signal remains active.'),
    ('duplicate_event_seconds', '30', 'Suppress repeated events for the same plate.'),
    ('event_image_retention_days', '90', 'Days to keep event snapshot files.'),
    ('unknown_plate_action', 'deny', 'Action for an unregistered or inactive plate.');
