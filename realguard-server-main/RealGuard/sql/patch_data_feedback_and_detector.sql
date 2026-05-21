-- RealGuard：图像检测表扩展（执行一次）
-- 数据库名以 imagedetection/views/utils.py 中 DB_CONFIG 为准（默认 system）

ALTER TABLE `data` ADD COLUMN `detector_probability` DOUBLE NULL DEFAULT NULL COMMENT '检测器原始AI概率' AFTER `fake`;
ALTER TABLE `data` ADD COLUMN `feedback` TINYINT NULL DEFAULT NULL COMMENT '1=满意 -1=不满意' AFTER `Userid`;
