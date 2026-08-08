-- 005 的回滚：删掉 home_media 表。
--
-- 什么时候用：首页图片配置功能整体下线，或要退回不认识这张表的旧版本服务。
--
-- ⚠️ 会丢掉后台配过的首页图**配置**（哪几张、什么顺序）。图片本身还在 COS 的
-- static/home-media/ 前缀下，不会被删，重新配一遍即可。要留底的话，删表前先导出：
--   \copy (SELECT slot, image_key, sort_order FROM home_media ORDER BY slot, sort_order)
--     TO 'home_media_backup.csv' CSV HEADER
--
-- 退回旧版本服务本身不需要跑这个：旧代码不引用这张表，留着也不影响。
-- 只有确认不再需要这份配置时才执行。
--
-- 幂等：DROP ... IF EXISTS，可重复执行。

BEGIN;

-- 表一删，它身上的索引和触发器跟着没了，不用单独 DROP
DROP TABLE IF EXISTS home_media;

COMMIT;

-- 验证：应返回 0 行
--   SELECT tablename FROM pg_tables WHERE tablename = 'home_media';
