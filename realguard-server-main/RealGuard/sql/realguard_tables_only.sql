-- 仅建表（需已存在 system 库）。供仅有 system.* 权限的账号执行，例如 root1。
-- 全量新环境更推荐管理员执行 sql/bootstrap_root1_admin.sql

USE `system`;

CREATE TABLE IF NOT EXISTS `user` (
  `Userid` INT NOT NULL AUTO_INCREMENT,
  `account_uuid` CHAR(36) NOT NULL COMMENT '不可变账号标识',
  `phone` VARCHAR(32) NOT NULL COMMENT '登录手机号',
  `secret` VARCHAR(255) NOT NULL COMMENT '密码',
  `username` VARCHAR(128) NULL DEFAULT NULL,
  `openid` VARCHAR(128) NULL DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `terms_version` VARCHAR(32) NULL DEFAULT NULL COMMENT '用户协议版本',
  `terms_accepted_at` DATETIME NULL DEFAULT NULL COMMENT '用户协议同意时间',
  `password_updated_at` DATETIME NULL DEFAULT NULL COMMENT '密码更新时间',
  PRIMARY KEY (`Userid`),
  UNIQUE KEY `uk_user_account_uuid` (`account_uuid`),
  UNIQUE KEY `uk_user_phone` (`phone`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户';

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

CREATE TABLE IF NOT EXISTS `admin_accounts` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `username` VARCHAR(64) NOT NULL,
  `phone` VARCHAR(20) NULL DEFAULT NULL,
  `password_hash` VARCHAR(255) NOT NULL,
  `role` VARCHAR(32) NOT NULL DEFAULT 'admin',
  `status` VARCHAR(16) NOT NULL DEFAULT 'active',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `last_login_at` DATETIME NULL DEFAULT NULL,
  `last_login_ip` VARCHAR(64) NULL DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_admin_accounts_username` (`username`),
  UNIQUE KEY `uk_admin_accounts_phone` (`phone`),
  KEY `idx_admin_accounts_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='后台管理员账号';

CREATE TABLE IF NOT EXISTS `data` (
  `itemid` INT NOT NULL AUTO_INCREMENT,
  `createtime` DATETIME NULL,
  `filename` VARCHAR(255) NULL,
  `fake` DOUBLE NULL COMMENT '综合/展示用 AI 概率 0~1',
  `detector_probability` DOUBLE NULL DEFAULT NULL COMMENT '检测器原始 AI 概率',
  `openid` VARCHAR(128) NULL,
  `phone` VARCHAR(32) NULL,
  `aigc` VARCHAR(64) NULL COMMENT '最终标签，如 真实图像/AI生成图像',
  `Fnumber` VARCHAR(64) NULL,
  `FocalLength` VARCHAR(64) NULL,
  `file_size` VARCHAR(64) NULL,
  `img_format` VARCHAR(32) NULL,
  `resolution` VARCHAR(64) NULL,
  `clarity` VARCHAR(255) NULL COMMENT '置信度等展示字段',
  `explantation` VARCHAR(512) NULL COMMENT '说明（字段名与代码一致）',
  `Userid` INT NULL,
  `owner_account_uuid` CHAR(36) NULL COMMENT 'system.user不可变账号标识',
  `feedback` TINYINT NULL DEFAULT NULL COMMENT '1=满意 -1=不满意',
  PRIMARY KEY (`itemid`),
  KEY `idx_data_phone_ct` (`phone`, `createtime`),
  KEY `idx_data_owner_uuid_ct` (`owner_account_uuid`, `createtime`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='图像鉴伪检测记录';

CREATE TABLE IF NOT EXISTS `exif` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `data_itemid` INT NOT NULL COMMENT '对应 data.itemid',
  `createtime` DATETIME NULL,
  `filename` VARCHAR(255) NULL,
  `openid` VARCHAR(128) NULL,
  `phone` VARCHAR(32) NULL,
  `Userid` INT NULL,
  `owner_account_uuid` CHAR(36) NULL COMMENT 'system.user不可变账号标识',
  `metadata_count` INT NULL,
  `has_ai_signal` TINYINT NULL,
  `has_real_signal` TINYINT NULL,
  `all_metadata` LONGTEXT NULL COMMENT 'JSON',
  `software` VARCHAR(255) NULL,
  `user_comment` TEXT NULL,
  `camera_make` VARCHAR(128) NULL,
  `camera_model` VARCHAR(128) NULL,
  `lens_model` VARCHAR(128) NULL,
  `lens_info` VARCHAR(255) NULL,
  `gps_position` VARCHAR(255) NULL,
  `datetime_original` VARCHAR(64) NULL,
  `exposure_time` VARCHAR(64) NULL,
  `fnumber` VARCHAR(64) NULL,
  `iso` VARCHAR(32) NULL,
  `focal_length` VARCHAR(64) NULL,
  PRIMARY KEY (`id`),
  KEY `idx_exif_data_itemid` (`data_itemid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='图像 EXIF 元数据';

CREATE TABLE IF NOT EXISTS `video_data` (
  `itemid` INT NOT NULL AUTO_INCREMENT,
  `createtime` DATETIME NULL,
  `filename` VARCHAR(255) NULL,
  `file_url` TEXT NULL,
  `source_type` VARCHAR(16) NULL COMMENT 'file/url',
  `fake_percentage` DOUBLE NULL,
  `real_percentage` DOUBLE NULL,
  `final_label` VARCHAR(64) NULL,
  `confidence_score` DOUBLE NULL,
  `confidence_level` VARCHAR(16) NULL,
  `explanation` VARCHAR(255) NULL,
  `d3_std` DOUBLE NULL,
  `encoder` VARCHAR(64) NULL,
  `frame_count` INT NULL,
  `file_size` VARCHAR(64) NULL,
  `duration` VARCHAR(64) NULL,
  `resolution` VARCHAR(64) NULL,
  `video_format` VARCHAR(32) NULL,
  `openid` VARCHAR(128) NULL,
  `phone` VARCHAR(32) NULL,
  `Userid` INT NULL,
  `owner_account_uuid` CHAR(36) NULL COMMENT 'system.user不可变账号标识',
  PRIMARY KEY (`itemid`),
  KEY `idx_video_data_phone_ct` (`phone`, `createtime`),
  KEY `idx_video_data_owner_uuid_ct` (`owner_account_uuid`, `createtime`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='视频鉴伪检测记录';

CREATE TABLE IF NOT EXISTS `legacy_record_governance` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `request_operation_id` CHAR(36) NOT NULL,
  `record_type` VARCHAR(16) NOT NULL,
  `record_id` BIGINT NOT NULL,
  `record_fingerprint` CHAR(64) NOT NULL,
  `media_sha256` CHAR(64) NOT NULL,
  `active_record_key` VARCHAR(64) NULL,
  `status` VARCHAR(24) NOT NULL DEFAULT 'claim_pending',
  `target_account_uuid` CHAR(36) NOT NULL,
  `target_user_id` BIGINT NOT NULL,
  `target_account_fingerprint` CHAR(64) NOT NULL,
  `evidence_reference` VARCHAR(512) NOT NULL,
  `evidence_sha256` CHAR(64) NOT NULL,
  `reason` VARCHAR(1000) NOT NULL,
  `requester_admin_id` BIGINT NOT NULL,
  `requester_username` VARCHAR(64) NOT NULL,
  `requester_identity_hash` CHAR(64) NOT NULL,
  `request_integrity_hmac` CHAR(64) NOT NULL,
  `requested_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `approval_operation_id` CHAR(36) NULL,
  `approver_admin_id` BIGINT NULL,
  `approver_username` VARCHAR(64) NULL,
  `approver_identity_hash` CHAR(64) NULL,
  `approval_integrity_hmac` CHAR(64) NULL,
  `decision_reason` VARCHAR(1000) NULL,
  `audit_key_id` VARCHAR(64) NOT NULL,
  `approved_at` DATETIME NULL,
  `version` INT NOT NULL DEFAULT 1,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_legacy_request_operation` (`request_operation_id`),
  UNIQUE KEY `uk_legacy_active_record` (`active_record_key`),
  UNIQUE KEY `uk_legacy_approval_operation` (`approval_operation_id`),
  KEY `idx_legacy_status_requested` (`status`, `requested_at`),
  KEY `idx_legacy_target_account` (`target_account_uuid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='历史遗留记录双人认领治理台账';

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
