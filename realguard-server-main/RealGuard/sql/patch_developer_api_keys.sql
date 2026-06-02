USE `system`;

CREATE TABLE IF NOT EXISTS `developer_api_keys` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `user_id` INT NOT NULL,
  `name` VARCHAR(120) NOT NULL,
  `key_hash` CHAR(64) NOT NULL,
  `key_prefix` VARCHAR(16) NOT NULL DEFAULT 'rg_sk_',
  `key_last4` CHAR(4) NOT NULL,
  `scopes` VARCHAR(255) NOT NULL DEFAULT 'detect,forensics,provenance,reports',
  `status` VARCHAR(16) NOT NULL DEFAULT 'active',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `last_used_at` DATETIME NULL,
  `revoked_at` DATETIME NULL,
  `last_used_ip` VARCHAR(64) NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_developer_api_key_hash` (`key_hash`),
  KEY `idx_developer_api_keys_user_status` (`user_id`, `status`),
  KEY `idx_developer_api_keys_created_at` (`created_at`),
  CONSTRAINT `fk_developer_api_keys_user`
    FOREIGN KEY (`user_id`) REFERENCES `user` (`Userid`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='开发者 API Key';
