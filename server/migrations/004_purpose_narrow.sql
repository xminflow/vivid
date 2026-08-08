-- 「预约需求」选项改版 —— 第 2 步：回填存量并收紧约束（发布新代码之后跑）
--
-- 前置条件：003 已执行，且新代码已经全量上线（小程序、官网、后台都不再写旧值）。
-- 提前跑这一步的后果：还在用旧值的客户端提交会 500。
--
-- ⚠️ 小程序要发版才会用上新值。发版前跑这一步，线上旧版本小程序的提交就会失败——
-- 所以顺序是：先发布服务端和官网 → 小程序发版 → 再跑这一步。
--
-- 幂等：UPDATE 带 WHERE 只命中旧值，跑第二遍是 0 行；约束重建结果一致。
-- 回滚：跑 004_rollback.sql。

BEGIN;

-- 先回填再收紧，顺序不能反：还有旧值时加新约束会直接失败
UPDATE appointments
   SET purpose = CASE purpose
       WHEN '装修与建材选购' THEN '装修建材订购'
       WHEN '家居产品选购'   THEN '家具软装选购'
       ELSE purpose
       END
 WHERE purpose IN ('装修与建材选购', '家居产品选购');

ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_purpose_check;

ALTER TABLE appointments ADD CONSTRAINT appointments_purpose_check
  CHECK (purpose IN (
    '展厅参观', '全案设计咨询', '装修建材订购', '家具软装选购', '商务合作', '其他'
  ));

COMMIT;

-- 验证：下面这条应返回 0
-- SELECT count(*) FROM appointments
--  WHERE purpose NOT IN ('展厅参观','全案设计咨询','装修建材订购','家具软装选购','商务合作','其他');
