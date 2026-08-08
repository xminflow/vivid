-- 「预约需求」选项改版 —— 第 1 步：放宽约束（发布新代码之前跑）
--
-- 新旧值对照：
--   装修与建材选购 → 装修建材订购
--   家居产品选购   → 家具软装选购
--   其余四项（展厅参观 / 全案设计咨询 / 商务合作 / 其他）不变
--
-- 分两步的原因同 001：CHECK 约束和应用代码不可能同一瞬间切换，中间要留一个
-- 「新旧都认」的过渡态，两头的代码在任何时刻都能写库。
--
-- 幂等：DROP ... IF EXISTS 之后重建，重复执行结果一致。
-- 回滚：跑 003_rollback.sql。
-- 执行完这一步后再发布新代码，然后跑 004_purpose_narrow.sql。

BEGIN;

ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_purpose_check;

ALTER TABLE appointments ADD CONSTRAINT appointments_purpose_check
  CHECK (purpose IN (
    -- 不变的四项
    '展厅参观', '全案设计咨询', '商务合作', '其他',
    -- 新值
    '装修建材订购', '家具软装选购',
    -- 旧值：过渡期继续放行，由 004 收紧掉
    '装修与建材选购', '家居产品选购'
  ));

COMMIT;

-- 验证：约束里应同时出现新旧两组值
-- SELECT pg_get_constraintdef(oid) FROM pg_constraint
--  WHERE conname = 'appointments_purpose_check';
