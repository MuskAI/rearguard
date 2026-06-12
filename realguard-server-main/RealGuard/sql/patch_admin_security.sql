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
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at DATETIME NULL,
  last_login_ip VARCHAR(64) NULL,
  KEY idx_admin_accounts_status (status),
  KEY idx_admin_accounts_phone (phone)
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
