USE `system`;

CREATE TABLE IF NOT EXISTS `privacy_erasure_jobs` (
  `job_id` CHAR(36) NOT NULL,
  `resource_kind` VARCHAR(16) NOT NULL,
  `resource_id` BIGINT NOT NULL,
  `owner_key_hash` CHAR(64) NOT NULL,
  `state` VARCHAR(24) NOT NULL DEFAULT 'preparing',
  `original_path` TEXT NULL,
  `staged_path` TEXT NULL,
  `thumbnail_original_path` TEXT NULL,
  `thumbnail_staged_path` TEXT NULL,
  `manifest_original_path` TEXT NULL,
  `manifest_staged_path` TEXT NULL,
  `attempt_count` INT UNSIGNED NOT NULL DEFAULT 0,
  `last_error` VARCHAR(255) NULL,
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  `completed_at` DATETIME(6) NULL,
  PRIMARY KEY (`job_id`),
  KEY `idx_privacy_erasure_resource` (`resource_kind`, `resource_id`),
  KEY `idx_privacy_erasure_state_updated` (`state`, `updated_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
