-- 016 的回滚：把 appointments.visit_time 删掉。
--
-- 什么时候用：新代码要退回旧版本。**退代码之后**再跑——顺序与 016 相反：
--   1. 小程序回退（新版本发出去了就撤不回，只能等下一版；这一列可空，
--      旧服务端收到 visitTime 会当成未知字段忽略，不会报错）
--   2. 服务端 + 后台退回旧版本
--   3. 再跑这个脚本
-- 提前跑的后果：还在跑的新服务端 INSERT/SELECT 到不存在的列，预约的提交和
-- 两个列表接口（/api/admin/appointments、/api/users/me/appointments）全部 500。
--
-- ⚠️ 这一步会丢掉列里的数据，回滚**不能**还原。执行前先留档：
--   CREATE TABLE appointments_visit_time_backup_016 AS
--     SELECT id, visit_time FROM appointments WHERE visit_time IS NOT NULL;
-- 之后要恢复：
--   ALTER TABLE appointments ADD COLUMN IF NOT EXISTS visit_time time;
--   UPDATE appointments a SET visit_time = b.visit_time
--     FROM appointments_visit_time_backup_016 b WHERE b.id = a.id;
--
-- 幂等：DROP COLUMN IF EXISTS，跑第二遍是空操作。

BEGIN;

ALTER TABLE appointments DROP COLUMN IF EXISTS visit_time;

COMMIT;

-- 验证：下面这条应返回 0
-- SELECT count(*) FROM information_schema.columns
--  WHERE table_schema = 'public'
--    AND table_name = 'appointments'
--    AND column_name = 'visit_time';
