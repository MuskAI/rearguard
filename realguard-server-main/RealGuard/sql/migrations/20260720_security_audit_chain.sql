CREATE TABLE IF NOT EXISTS `security_audit_chain_head` (
  `id` TINYINT NOT NULL,
  `last_event_hash` CHAR(64) NOT NULL,
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='安全审计哈希链并发锁';

CREATE TABLE IF NOT EXISTS `security_audit_events` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `event_id` CHAR(36) NOT NULL,
  `occurred_at` VARCHAR(32) NOT NULL,
  `actor_type` VARCHAR(32) NOT NULL,
  `actor_id` VARCHAR(64) NOT NULL,
  `action` VARCHAR(96) NOT NULL,
  `target` VARCHAR(191) NOT NULL,
  `meta_json` LONGTEXT NOT NULL,
  `previous_hash` CHAR(64) NOT NULL,
  `event_hash` CHAR(64) NOT NULL,
  `key_id` VARCHAR(64) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_security_audit_event_id` (`event_id`),
  UNIQUE KEY `uk_security_audit_event_hash` (`event_hash`),
  KEY `idx_security_audit_actor_time` (`actor_type`, `actor_id`, `occurred_at`),
  KEY `idx_security_audit_action_time` (`action`, `occurred_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='带 HMAC 哈希链的敏感操作审计';
