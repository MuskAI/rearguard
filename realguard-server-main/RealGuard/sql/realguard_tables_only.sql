-- 仅建表（需已存在 system 库）。供仅有 system.* 权限的账号执行，例如 root1。
-- 全量新环境更推荐管理员执行 sql/bootstrap_root1_admin.sql

USE `system`;

CREATE TABLE IF NOT EXISTS `user` (
  `Userid` INT NOT NULL AUTO_INCREMENT,
  `phone` VARCHAR(32) NOT NULL COMMENT '登录手机号',
  `secret` VARCHAR(255) NOT NULL COMMENT '密码',
  `username` VARCHAR(128) NULL DEFAULT NULL,
  `openid` VARCHAR(128) NULL DEFAULT NULL,
  PRIMARY KEY (`Userid`),
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
  `feedback` TINYINT NULL DEFAULT NULL COMMENT '1=满意 -1=不满意',
  PRIMARY KEY (`itemid`),
  KEY `idx_data_phone_ct` (`phone`, `createtime`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='图像鉴伪检测记录';

CREATE TABLE IF NOT EXISTS `exif` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `data_itemid` INT NOT NULL COMMENT '对应 data.itemid',
  `createtime` DATETIME NULL,
  `filename` VARCHAR(255) NULL,
  `openid` VARCHAR(128) NULL,
  `phone` VARCHAR(32) NULL,
  `Userid` INT NULL,
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

CREATE TABLE IF NOT EXISTS `retrieve_data` (
  `itemid` INT NOT NULL AUTO_INCREMENT,
  `createtime` DATETIME NULL,
  `filename` VARCHAR(255) NULL,
  `search_type` VARCHAR(16) NULL COMMENT 'image / video',
  `result_count` INT NULL,
  `top_k` INT NULL,
  `openid` VARCHAR(128) NULL,
  `phone` VARCHAR(32) NULL,
  `file_size` VARCHAR(64) NULL,
  `results_json` LONGTEXT NULL COMMENT '检索结果 JSON',
  `Userid` INT NULL,
  PRIMARY KEY (`itemid`),
  KEY `idx_retrieve_phone_ct_type` (`phone`, `createtime`, `search_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='侵权检索历史';

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
  PRIMARY KEY (`itemid`),
  KEY `idx_video_data_phone_ct` (`phone`, `createtime`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='视频鉴伪检测记录';
