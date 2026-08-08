-- 「来者身份」选项改版 —— 第 2 步：回填存量并收紧约束（发布新代码之后跑）
--
-- 前置条件：001 已执行，且新代码已经全量上线（小程序、官网、后台都不再写旧值）。
-- 提前跑这一步的后果：还在用旧值的客户端提交会 500。
--
-- 幂等：UPDATE 带 WHERE 只命中旧值，跑第二遍是 0 行；约束重建结果一致。
-- 回滚：跑 002_rollback.sql（把新值改回旧值并恢复旧约束）。

BEGIN;

-- 先回填再收紧，顺序不能反：还有旧值时加新约束会直接失败
UPDATE appointments
   SET visitor_type = CASE visitor_type
       WHEN 'C端业主'        THEN '业主'
       WHEN '地产开发商'     THEN '地产圈'
       WHEN '家居行业经销商' THEN '家居圈'
       WHEN '酒店民宿业主'   THEN '酒店民宿圈'
       WHEN '艺术家'         THEN '艺术圈'
       ELSE visitor_type
       END
 WHERE visitor_type IN ('C端业主', '地产开发商', '家居行业经销商', '酒店民宿业主', '艺术家');

ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_visitor_type_check;

ALTER TABLE appointments ADD CONSTRAINT appointments_visitor_type_check
  CHECK (visitor_type IN ('业主', '设计师', '地产圈', '家居圈', '酒店民宿圈', '艺术圈'));

COMMIT;

-- 验证：下面两条都应返回 0 行
-- SELECT count(*) FROM appointments
--  WHERE visitor_type NOT IN ('业主','设计师','地产圈','家居圈','酒店民宿圈','艺术圈');
-- SELECT 1 FROM pg_constraint
--  WHERE conname = 'appointments_visitor_type_check'
--    AND pg_get_constraintdef(oid) LIKE '%C端业主%';
