-- RealGuard 全量建库建表（新环境部署执行一次即可）
-- 数据库名须与 imagedetection/views/utils.py 中 DB_CONFIG['database'] 一致（默认 system）

CREATE DATABASE IF NOT EXISTS `system`
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE `system`;

-- ---------------------------------------------------------------------------
-- 用户表
-- ---------------------------------------------------------------------------
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
  `session_version` INT NOT NULL DEFAULT 1 COMMENT '登录态版本，重置密码时递增',
  PRIMARY KEY (`Userid`),
  UNIQUE KEY `uk_user_account_uuid` (`account_uuid`),
  UNIQUE KEY `uk_user_phone` (`phone`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户';

CREATE TABLE IF NOT EXISTS `consent_events` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `user_id` INT NOT NULL,
  `phone_hash` CHAR(64) NOT NULL,
  `document_version` VARCHAR(32) NOT NULL,
  `terms_sha256` CHAR(64) NOT NULL,
  `privacy_sha256` CHAR(64) NOT NULL,
  `channel` VARCHAR(64) NOT NULL,
  `client_ip_hash` CHAR(64) NULL,
  `user_agent_hash` CHAR(64) NULL,
  `accepted_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_consent_events_user_time` (`user_id`, `accepted_at`),
  CONSTRAINT `fk_consent_events_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`Userid`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='不可覆盖的协议同意事件';

-- ---------------------------------------------------------------------------
-- 开发者 API Key（仅保存哈希和预览，完整 key 只在创建时返回一次）
-- ---------------------------------------------------------------------------
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
  `expires_at` DATETIME NULL,
  `ip_allowlist` TEXT NULL,
  `last_used_ip` VARCHAR(64) NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_developer_api_key_hash` (`key_hash`),
  KEY `idx_developer_api_keys_user_status` (`user_id`, `status`),
  KEY `idx_developer_api_keys_created_at` (`created_at`),
  CONSTRAINT `fk_developer_api_keys_user`
    FOREIGN KEY (`user_id`) REFERENCES `user` (`Userid`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='开发者 API Key';

CREATE TABLE IF NOT EXISTS `developer_api_account_quotas` (
  `user_id` INT NOT NULL,
  `daily_limit` INT NULL,
  `rate_limit_per_minute` INT NULL,
  `scopes` VARCHAR(255) NULL,
  `notes` TEXT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`user_id`),
  CONSTRAINT `fk_developer_api_account_quotas_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`Userid`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='账号级 API 配额';

CREATE TABLE IF NOT EXISTS `developer_api_account_quota_usage` (
  `user_id` INT NOT NULL,
  `day_bucket` DATE NOT NULL,
  `daily_count` BIGINT UNSIGNED NOT NULL DEFAULT 0,
  `minute_bucket` DATETIME NOT NULL,
  `minute_count` INT UNSIGNED NOT NULL DEFAULT 0,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`user_id`),
  CONSTRAINT `fk_developer_api_account_quota_usage_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`Userid`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='账号级 API 配额计数';

-- ---------------------------------------------------------------------------
-- 后台管理员账号（独立于普通用户登录）
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- 图像检测记录（含 detector_probability、feedback，等价于执行过 patch_data_feedback_and_detector.sql）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `data` (
  `itemid` INT NOT NULL AUTO_INCREMENT,
  `createtime` DATETIME NULL,
  `filename` VARCHAR(255) NULL,
  `fake` DOUBLE NULL COMMENT '综合/展示用风险百分数 0~100',
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

-- ---------------------------------------------------------------------------
-- EXIF 元数据（关联 data.itemid）
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- 视频鉴伪检测记录（与 sql/patch_video_data.sql 一致）
-- ---------------------------------------------------------------------------
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
