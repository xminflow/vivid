-- 007 的回滚：把两张表的 status 列加回来。
--
-- 什么时候用：新代码上线后要退回旧版本。**退代码之前**先跑这个，否则旧代码的
-- SELECT 里带着 status，两个列表接口（后台的和小程序「我的预约」的）全部 500。
--
-- ⚠️ 只还原**结构**，不还原取值：删列时每行的值已经没了，这里一律按 'new' 重建。
-- 执行 007 前若确认过全表都是 'new'（那个脚本里有验证 SQL），重建结果与删除前
-- 逐行一致；否则被跟进过的记录会退回「待跟进」，要从 007 里那两张备份表回填：
--   UPDATE appointments a SET status = b.status
--     FROM appointments_status_backup_007 b WHERE b.id = a.id;
--
-- 幂等：ADD COLUMN IF NOT EXISTS + DROP CONSTRAINT IF EXISTS，可重复执行。

BEGIN;

-- 分两步加列再加约束，与 schema.sql 里原来的内联写法等价：内联 CHECK 由
-- Postgres 自动命名成 <表名>_status_check，这里显式用同一个名字，重建出来的
-- 库结构和删除前对得上
ALTER TABLE appointments
  ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'new';
ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_status_check;
ALTER TABLE appointments ADD CONSTRAINT appointments_status_check
  CHECK (status IN ('new', 'confirmed', 'visited', 'cancelled'));

ALTER TABLE service_applications
  ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'new';
ALTER TABLE service_applications DROP CONSTRAINT IF EXISTS service_applications_status_check;
ALTER TABLE service_applications ADD CONSTRAINT service_applications_status_check
  CHECK (status IN ('new', 'contacted', 'closed'));

COMMIT;

-- 验证：下面这条应返回 2
-- SELECT count(*) FROM information_schema.columns
--  WHERE table_schema = 'public'
--    AND table_name IN ('appointments', 'service_applications')
--    AND column_name = 'status';
