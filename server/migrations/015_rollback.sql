-- 015 的回滚：删掉每日配额表。
--
-- 什么时候用：新代码要退回旧版本。**退代码之后**再跑——顺序与 015 相反：
--   1. 小程序回退（不回退也行：老版本没有「微信获取」按钮，不会调这个接口）
--   2. 服务端退回旧版本
--   3. 再跑这个脚本
-- 提前跑的后果：还在跑的新服务端一调换手机号接口就 500（表不存在）。
--
-- ⚠️ 这张表里只有计数，删掉不丢业务数据；但删掉当天已用过配额的人会被清零，
-- 相当于当天配额重新发一遍。要留档就先跑：
--   SELECT user_id, quota_date, used FROM user_phone_quota WHERE used > 0;
--
-- 幂等：DROP TABLE IF EXISTS，可重复执行。

BEGIN;

DROP TABLE IF EXISTS user_phone_quota;

COMMIT;

-- 验证：表没了（应返回 0 行）
--   SELECT tablename FROM pg_tables WHERE tablename = 'user_phone_quota';
