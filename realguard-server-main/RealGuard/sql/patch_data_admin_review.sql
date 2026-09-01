-- Keep administrator review separate from the user's helpful / unhelpful vote.
ALTER TABLE `data`
  ADD COLUMN `admin_review` TINYINT NULL DEFAULT NULL
  COMMENT '管理员复核：1=正确 -1=误判' AFTER `feedback`;
