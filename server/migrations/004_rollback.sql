-- 004 的回滚：把新值改回旧值，并把约束恢复成只认旧值。
--
-- 什么时候用：新代码上线后要退回旧版本。退代码之前先跑这个，
-- 否则旧代码读到「装修建材订购」这类它不认识的值，筛选和展示都会错。
--
-- 幂等：UPDATE 带 WHERE 只命中新值；约束重建结果一致。

BEGIN;

-- 先去掉约束，否则下面 UPDATE 的中间态会撞上它
ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_purpose_check;

UPDATE appointments
   SET purpose = CASE purpose
       WHEN '装修建材订购' THEN '装修与建材选购'
       WHEN '家具软装选购' THEN '家居产品选购'
       ELSE purpose
       END
 WHERE purpose IN ('装修建材订购', '家具软装选购');

ALTER TABLE appointments ADD CONSTRAINT appointments_purpose_check
  CHECK (purpose IN (
    '展厅参观', '全案设计咨询', '装修与建材选购', '家居产品选购', '商务合作', '其他'
  ));

COMMIT;
