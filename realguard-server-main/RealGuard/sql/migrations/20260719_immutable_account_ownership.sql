-- Applied by `flask --app run:app identity-db-upgrade` because production
-- stores accounts and detection history in separate databases. This file
-- documents the resulting columns for operators and fresh schema reviews.
ALTER TABLE `user`
  ADD COLUMN `account_uuid` CHAR(36) NULL COMMENT '不可变账号标识' AFTER `Userid`;

UPDATE `user`
SET account_uuid = UUID()
WHERE account_uuid IS NULL OR account_uuid = '';

ALTER TABLE `user`
  MODIFY COLUMN `account_uuid` CHAR(36) NOT NULL COMMENT '不可变账号标识',
  ADD UNIQUE KEY `uk_user_account_uuid` (`account_uuid`);

-- Run the following against the image_detection database.
ALTER TABLE `user`
  ADD COLUMN `account_uuid` CHAR(36) NULL COMMENT 'system.user不可变账号标识' AFTER `Userid`,
  ADD UNIQUE KEY `uk_detection_user_account_uuid` (`account_uuid`);

ALTER TABLE `data`
  ADD COLUMN `owner_account_uuid` CHAR(36) NULL COMMENT 'system.user不可变账号标识' AFTER `Userid`,
  ADD KEY `idx_data_owner_uuid_ct` (`owner_account_uuid`, `createtime`);

ALTER TABLE `video_data`
  ADD COLUMN `owner_account_uuid` CHAR(36) NULL COMMENT 'system.user不可变账号标识' AFTER `Userid`,
  ADD KEY `idx_video_data_owner_uuid_ct` (`owner_account_uuid`, `createtime`);

-- Existing developer tasks stay fail-closed until an operator verifies and
-- assigns their immutable account UUID.
ALTER TABLE `developer_detection_tasks`
  ADD COLUMN `account_uuid` CHAR(36) NULL COMMENT '不可变账号标识' AFTER `user_id`,
  ADD COLUMN `mime_type` VARCHAR(127) NOT NULL DEFAULT 'application/octet-stream' AFTER `filename`,
  ADD COLUMN `execution_filename` VARCHAR(255) NULL AFTER `mime_type`,
  ADD COLUMN `spool_path` VARCHAR(255) NULL AFTER `request_sha256`,
  ADD COLUMN `spool_size` BIGINT UNSIGNED NULL AFTER `spool_path`,
  ADD COLUMN `request_context_json` TEXT NULL AFTER `spool_size`,
  ADD COLUMN `lease_owner` VARCHAR(64) NULL AFTER `status`,
  ADD COLUMN `lease_expires_at` DATETIME(6) NULL AFTER `lease_owner`,
  ADD COLUMN `attempt_count` INT UNSIGNED NOT NULL DEFAULT 0 AFTER `lease_expires_at`,
  ADD COLUMN `last_heartbeat_at` DATETIME(6) NULL AFTER `attempt_count`,
  ADD COLUMN `effect_item_id` INT NULL AFTER `last_heartbeat_at`,
  ADD COLUMN `effect_result_json` MEDIUMTEXT NULL AFTER `effect_item_id`,
  ADD COLUMN `daily_quota_reserved` TINYINT(1) NOT NULL DEFAULT 0 AFTER `effect_result_json`,
  ADD COLUMN `daily_quota_day` DATE NULL AFTER `daily_quota_reserved`,
  ADD COLUMN `prompt_tokens` INT UNSIGNED NOT NULL DEFAULT 0 AFTER `daily_quota_day`,
  ADD COLUMN `completion_tokens` INT UNSIGNED NOT NULL DEFAULT 0 AFTER `prompt_tokens`,
  ADD COLUMN `total_tokens` INT UNSIGNED NOT NULL DEFAULT 0 AFTER `completion_tokens`,
  MODIFY COLUMN `status` VARCHAR(24) NOT NULL DEFAULT 'preparing',
  DROP INDEX `uk_developer_task_idempotency`,
  ADD UNIQUE KEY `uk_developer_task_idempotency` (`account_uuid`, `idempotency_key`),
  ADD KEY `idx_developer_tasks_lease` (`status`, `lease_expires_at`);

ALTER TABLE `developer_usage_events`
  ADD COLUMN `task_id` VARCHAR(64) NULL COMMENT '可幂等恢复的开发者任务标识' AFTER `id`,
  ADD UNIQUE KEY `uk_developer_usage_task` (`task_id`);
