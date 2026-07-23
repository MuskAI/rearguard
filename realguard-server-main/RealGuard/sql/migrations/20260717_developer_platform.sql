USE `system`;

CREATE TABLE IF NOT EXISTS `developer_api_keys` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `user_id` INT NOT NULL,
  `name` VARCHAR(120) NOT NULL,
  `key_hash` CHAR(64) NOT NULL,
  `key_prefix` VARCHAR(16) NOT NULL DEFAULT 'rg_sk_',
  `key_last4` CHAR(4) NOT NULL,
  `scopes` VARCHAR(255) NOT NULL DEFAULT 'image:fast,image:swarm,reports',
  `status` VARCHAR(16) NOT NULL DEFAULT 'active',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `last_used_at` DATETIME NULL,
  `revoked_at` DATETIME NULL,
  `expires_at` DATETIME NULL,
  `ip_allowlist` TEXT NULL,
  `last_used_ip` VARCHAR(64) NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_developer_api_key_hash` (`key_hash`),
  KEY `idx_developer_api_keys_user_status` (`user_id`, `status`),
  KEY `idx_developer_api_keys_created_at` (`created_at`),
  CONSTRAINT `fk_developer_api_keys_user`
    FOREIGN KEY (`user_id`) REFERENCES `user` (`Userid`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET @add_expires_at = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'developer_api_keys' AND COLUMN_NAME = 'expires_at') = 0,
  'ALTER TABLE `developer_api_keys` ADD COLUMN `expires_at` DATETIME NULL',
  'SELECT 1'
);
PREPARE statement FROM @add_expires_at;
EXECUTE statement;
DEALLOCATE PREPARE statement;

SET @add_ip_allowlist = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'developer_api_keys' AND COLUMN_NAME = 'ip_allowlist') = 0,
  'ALTER TABLE `developer_api_keys` ADD COLUMN `ip_allowlist` TEXT NULL',
  'SELECT 1'
);
PREPARE statement FROM @add_ip_allowlist;
EXECUTE statement;
DEALLOCATE PREPARE statement;

ALTER TABLE `developer_api_keys`
  MODIFY COLUMN `scopes` VARCHAR(255) NOT NULL DEFAULT 'image:fast,image:swarm,reports';

-- Legacy `detect` keys were fast-detection keys. Do not grant Swarm or report
-- access during migration; operators can explicitly issue a new key instead.
UPDATE `developer_api_keys`
SET `scopes` = CASE
  WHEN FIND_IN_SET('reports', `scopes`) > 0 THEN 'image:fast,reports'
  ELSE 'image:fast'
END
WHERE FIND_IN_SET('detect', `scopes`) > 0;

CREATE TABLE IF NOT EXISTS `developer_usage_events` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `task_id` VARCHAR(64) NULL,
  `user_id` INT NOT NULL,
  `key_id` BIGINT NULL,
  `pipeline` VARCHAR(32) NOT NULL,
  `endpoint` VARCHAR(160) NOT NULL,
  `model_version` VARCHAR(120) NULL,
  `status_code` INT NOT NULL DEFAULT 200,
  `prompt_tokens` INT NOT NULL DEFAULT 0,
  `completion_tokens` INT NOT NULL DEFAULT 0,
  `total_tokens` INT NOT NULL DEFAULT 0,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_developer_usage_task` (`task_id`),
  KEY `idx_developer_usage_user_created` (`user_id`, `created_at`),
  KEY `idx_developer_usage_key_created` (`key_id`, `created_at`),
  KEY `idx_developer_usage_pipeline_created` (`pipeline`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `developer_accounts` (
  `user_id` INT NOT NULL,
  `status` VARCHAR(16) NOT NULL DEFAULT 'active',
  `free_total` INT NOT NULL DEFAULT 100,
  `free_used` INT NOT NULL DEFAULT 0,
  `free_reserved` INT NOT NULL DEFAULT 0,
  `balance_fen` BIGINT NOT NULL DEFAULT 0,
  `balance_reserved_fen` BIGINT NOT NULL DEFAULT 0,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`user_id`),
  CONSTRAINT `chk_developer_accounts_nonnegative` CHECK (
    `free_total` >= 0 AND `free_used` >= 0 AND `free_reserved` >= 0
    AND `balance_fen` >= 0 AND `balance_reserved_fen` >= 0
  ),
  CONSTRAINT `chk_developer_accounts_reservations` CHECK (
    `free_used` + `free_reserved` <= `free_total`
    AND `balance_reserved_fen` <= `balance_fen`
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `developer_pricing` (
  `mode` VARCHAR(16) NOT NULL,
  `display_name` VARCHAR(64) NOT NULL,
  `unit_price_fen` INT NOT NULL DEFAULT 0,
  `enabled` TINYINT(1) NOT NULL DEFAULT 0,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`mode`),
  CONSTRAINT `chk_developer_pricing_values` CHECK (
    `mode` IN ('fast', 'swarm') AND `unit_price_fen` >= 0 AND `enabled` IN (0, 1)
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO `developer_pricing` (`mode`, `display_name`, `unit_price_fen`, `enabled`) VALUES
  ('fast', '快速检测', 0, 0),
  ('swarm', 'Swarm 多源复核', 0, 0);

CREATE TABLE IF NOT EXISTS `developer_detection_tasks` (
  `task_id` VARCHAR(64) NOT NULL,
  `user_id` INT NOT NULL,
  `account_uuid` CHAR(36) NOT NULL,
  `key_id` BIGINT NOT NULL,
  `mode` VARCHAR(16) NOT NULL,
  `filename` VARCHAR(255) NOT NULL,
  `mime_type` VARCHAR(127) NOT NULL DEFAULT 'application/octet-stream',
  `execution_filename` VARCHAR(255) NULL,
  `request_sha256` CHAR(64) NOT NULL,
  `spool_path` VARCHAR(255) NULL,
  `spool_size` BIGINT UNSIGNED NULL,
  `request_context_json` TEXT NULL,
  `idempotency_key` VARCHAR(128) NULL,
  `status` VARCHAR(24) NOT NULL DEFAULT 'preparing',
  `lease_owner` VARCHAR(64) NULL,
  `lease_expires_at` DATETIME(6) NULL,
  `attempt_count` INT UNSIGNED NOT NULL DEFAULT 0,
  `last_heartbeat_at` DATETIME(6) NULL,
  `effect_item_id` INT NULL,
  `effect_result_json` MEDIUMTEXT NULL,
  `daily_quota_reserved` TINYINT(1) NOT NULL DEFAULT 0,
  `daily_quota_day` DATE NULL,
  `prompt_tokens` INT UNSIGNED NOT NULL DEFAULT 0,
  `completion_tokens` INT UNSIGNED NOT NULL DEFAULT 0,
  `total_tokens` INT UNSIGNED NOT NULL DEFAULT 0,
  `result_item_id` INT NULL,
  `result_json` MEDIUMTEXT NULL,
  `error_message` VARCHAR(500) NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `completed_at` DATETIME NULL,
  PRIMARY KEY (`task_id`),
  UNIQUE KEY `uk_developer_task_idempotency` (`account_uuid`, `idempotency_key`),
  KEY `idx_developer_tasks_user_created` (`user_id`, `created_at`),
  KEY `idx_developer_tasks_key_created` (`key_id`, `created_at`),
  KEY `idx_developer_tasks_lease` (`status`, `lease_expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `developer_billing_reservations` (
  `task_id` VARCHAR(64) NOT NULL,
  `user_id` INT NOT NULL,
  `key_id` BIGINT NOT NULL,
  `mode` VARCHAR(16) NOT NULL,
  `source` VARCHAR(16) NOT NULL,
  `amount_fen` INT NOT NULL DEFAULT 0,
  `status` VARCHAR(16) NOT NULL DEFAULT 'reserved',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `settled_at` DATETIME NULL,
  `released_at` DATETIME NULL,
  PRIMARY KEY (`task_id`),
  KEY `idx_developer_reservations_user_created` (`user_id`, `created_at`),
  KEY `idx_developer_reservations_status` (`status`, `created_at`),
  CONSTRAINT `chk_developer_reservation_values` CHECK (
    `mode` IN ('fast', 'swarm')
    AND `source` IN ('free', 'balance')
    AND `status` IN ('reserved', 'settled', 'released')
    AND `amount_fen` >= 0
    AND (`source` <> 'free' OR `amount_fen` = 0)
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `developer_billing_ledger` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `user_id` INT NOT NULL,
  `key_id` BIGINT NULL,
  `task_id` VARCHAR(64) NULL,
  `operation_id` VARCHAR(128) NULL,
  `business_reference` VARCHAR(191) NOT NULL,
  `currency` CHAR(3) NOT NULL DEFAULT 'CNY',
  `reversal_of_id` BIGINT NULL,
  `entry_type` VARCHAR(32) NOT NULL,
  `mode` VARCHAR(16) NULL,
  `free_calls_delta` INT NOT NULL DEFAULT 0,
  `free_calls_after` INT NULL,
  `balance_delta_fen` BIGINT NOT NULL DEFAULT 0,
  `amount_fen` INT NOT NULL DEFAULT 0,
  `balance_after_fen` BIGINT NOT NULL DEFAULT 0,
  `note` VARCHAR(500) NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_developer_ledger_business_reference` (`business_reference`),
  KEY `idx_developer_ledger_user_created` (`user_id`, `created_at`),
  KEY `idx_developer_ledger_task` (`task_id`),
  KEY `idx_developer_ledger_operation` (`operation_id`),
  KEY `idx_developer_ledger_reversal` (`reversal_of_id`),
  CONSTRAINT `chk_developer_ledger_values` CHECK (
    `amount_fen` >= 0 AND `balance_after_fen` >= 0
    AND (`free_calls_after` IS NULL OR `free_calls_after` >= 0)
    AND `currency` = 'CNY'
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `developer_admin_operations` (
  `operation_id` VARCHAR(128) NOT NULL,
  `operation_type` VARCHAR(32) NOT NULL,
  `user_id` INT NOT NULL,
  `request_sha256` CHAR(64) NOT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`operation_id`),
  KEY `idx_developer_admin_operations_user_created` (`user_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `developer_admin_operation_results` (
  `operation_id` VARCHAR(128) NOT NULL,
  `status_code` INT NOT NULL DEFAULT 200,
  `response_json` MEDIUMTEXT NOT NULL,
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`operation_id`),
  CONSTRAINT `fk_developer_admin_operation_results_operation`
    FOREIGN KEY (`operation_id`) REFERENCES `developer_admin_operations` (`operation_id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `admin_audit_logs` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `actor_id` VARCHAR(64) NULL,
  `actor_username` VARCHAR(64) NULL,
  `actor_phone` VARCHAR(20) NULL,
  `action` VARCHAR(96) NOT NULL,
  `target` VARCHAR(191) NOT NULL,
  `before_json` LONGTEXT NULL,
  `after_json` LONGTEXT NULL,
  `meta_json` LONGTEXT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_admin_audit_created` (`created_at`),
  KEY `idx_admin_audit_action` (`action`),
  KEY `idx_admin_audit_target` (`target`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
