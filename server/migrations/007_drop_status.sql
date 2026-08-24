-- 「跟进状态」下线 —— 删掉两张表的 status 列（发布新代码**之后**跑）
--
-- 背景：status 从建库起就只有默认值 'new'，没有任何写入口——后台只能按它筛选，
-- 改不了；小程序那边也只是把 'new' 翻成「待确认」显示。等于一个永远不变的字段。
-- 需求方决定不做状态流转，改用「删除记录」清理已处理的线索，所以这一列直接删掉。
--
-- 前置条件：**新代码必须先全量上线**，顺序不能反：
--   1. 发布服务端 + 后台（新代码的 SELECT 里不再有 status，带着这一列也照常跑）
--   2. 小程序发版（旧版本读不到 status 只是不显示状态签，不会报错，但一并发了更干净）
--   3. 再跑这个脚本
-- 提前跑的后果：线上旧版本服务端的两个列表接口（/api/admin/appointments、
-- /api/users/me/appointments）SELECT 到不存在的列，直接 500。
--
-- ⚠️ 这一步会丢掉列里的数据，且回滚**不能**还原取值。执行前先确认下面两条都返回 0：
--   SELECT count(*) FROM appointments WHERE status <> 'new';
--   SELECT count(*) FROM service_applications WHERE status <> 'new';
-- 全为 0 时，007_rollback.sql 重建出来的列与删除前逐行一致，等于可完整回滚。
-- 不为 0 时，先把那几行导出留档（见文件末尾的备份 SQL）再决定。
--
-- 幂等：DROP COLUMN IF EXISTS，跑第二遍是空操作。
-- 回滚：跑 007_rollback.sql。

BEGIN;

-- 列上的 CHECK 约束随列一起消失，不用单独 DROP CONSTRAINT
ALTER TABLE appointments         DROP COLUMN IF EXISTS status;
ALTER TABLE service_applications DROP COLUMN IF EXISTS status;

COMMIT;

-- 验证：下面这条应返回 0
-- SELECT count(*) FROM information_schema.columns
--  WHERE table_schema = 'public'
--    AND table_name IN ('appointments', 'service_applications')
--    AND column_name = 'status';

-- 留档备份（只在上面的确认查询不为 0 时才需要，在 DROP 之前跑）：
-- CREATE TABLE appointments_status_backup_007 AS
--   SELECT id, status FROM appointments WHERE status <> 'new';
-- CREATE TABLE service_applications_status_backup_007 AS
--   SELECT id, status FROM service_applications WHERE status <> 'new';
