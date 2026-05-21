-- RealGuard：视频检测记录表（执行一次）
-- 数据库名以 imagedetection/views/utils.py 中 DB_CONFIG 为准（默认 system）

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
