-- RealGuard admin security and operations schema.
-- Run against the system database first:
--   mysql -u <user> -p system < sql/patch_admin_security.sql

CREATE TABLE IF NOT EXISTS admin_accounts (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(64) NOT NULL UNIQUE,
  phone VARCHAR(20) NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  role VARCHAR(32) NOT NULL DEFAULT 'admin',
  status VARCHAR(16) NOT NULL DEFAULT 'active',
  session_version INT NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at DATETIME NULL,
  last_login_ip VARCHAR(64) NULL,
  KEY idx_admin_accounts_status (status),
  KEY idx_admin_accounts_role_status (role, status),
  KEY idx_admin_accounts_phone (phone)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

SET @rg_has_session_version := (
  SELECT COUNT(*)
  FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'admin_accounts'
    AND COLUMN_NAME = 'session_version'
);
SET @rg_session_version_sql := IF(
  @rg_has_session_version = 0,
  'ALTER TABLE admin_accounts ADD COLUMN session_version INT NOT NULL DEFAULT 1 AFTER status',
  'SELECT 1'
);
PREPARE rg_session_version_stmt FROM @rg_session_version_sql;
EXECUTE rg_session_version_stmt;
DEALLOCATE PREPARE rg_session_version_stmt;

SET @rg_has_role_status_index := (
  SELECT COUNT(*)
  FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'admin_accounts'
    AND INDEX_NAME = 'idx_admin_accounts_role_status'
);
SET @rg_role_status_index_sql := IF(
  @rg_has_role_status_index = 0,
  'CREATE INDEX idx_admin_accounts_role_status ON admin_accounts (role, status)',
  'SELECT 1'
);
PREPARE rg_role_status_index_stmt FROM @rg_role_status_index_sql;
EXECUTE rg_role_status_index_stmt;
DEALLOCATE PREPARE rg_role_status_index_stmt;

CREATE TABLE IF NOT EXISTS admin_login_attempts (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  identity_hash CHAR(64) NOT NULL,
  ip_hash CHAR(64) NOT NULL,
  failure_count INT NOT NULL DEFAULT 0,
  locked_until_epoch BIGINT NOT NULL DEFAULT 0,
  last_failed_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uniq_admin_login_attempts (identity_hash, ip_hash),
  KEY idx_admin_login_attempts_locked (locked_until_epoch)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS admin_audit_logs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  actor_id VARCHAR(64) NULL,
  actor_username VARCHAR(64) NULL,
  actor_phone VARCHAR(20) NULL,
  action VARCHAR(96) NOT NULL,
  target VARCHAR(191) NOT NULL,
  before_json LONGTEXT NULL,
  after_json LONGTEXT NULL,
  meta_json LONGTEXT NULL,
  KEY idx_admin_audit_created (created_at),
  KEY idx_admin_audit_action (action),
  KEY idx_admin_audit_target (target)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS admin_model_runs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  run_id VARCHAR(64) NOT NULL UNIQUE,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  itemid BIGINT NULL,
  route VARCHAR(32) NOT NULL DEFAULT 'primary',
  status VARCHAR(32) NOT NULL DEFAULT 'success',
  model_id VARCHAR(96) NULL,
  model_name VARCHAR(191) NULL,
  model_runtime VARCHAR(96) NULL,
  model_endpoint VARCHAR(512) NULL,
  model_version VARCHAR(96) NULL,
  actor_id VARCHAR(64) NULL,
  actor_username VARCHAR(64) NULL,
  actor_phone VARCHAR(20) NULL,
  meta_json LONGTEXT NULL,
  KEY idx_admin_model_runs_itemid (itemid),
  KEY idx_admin_model_runs_created (created_at),
  KEY idx_admin_model_runs_model (model_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Optional performance indexes for the image_detection database.
-- Execute these after switching to image_detection. If an index already exists,
-- MySQL will report a duplicate-name error; keep only missing indexes.
-- CREATE INDEX idx_data_createtime ON data (createtime);
-- CREATE INDEX idx_data_phone ON data (phone);
-- CREATE INDEX idx_data_aigc ON data (aigc);
-- CREATE INDEX idx_video_data_createtime ON video_data (createtime);
