-- 002 的回滚：把新值改回旧值，并把约束恢复成只认旧值。
--
-- 什么时候用：新代码上线后发现问题要退回旧版本。退代码之前先跑这个，
-- 否则旧代码读到「地产圈」这类它不认识的值，筛选和展示都会错。
--
-- 注意「业主」的反向映射是有损的：新值只有一个「业主」，旧值里对应的是
-- 「C端业主」，这一项能一一对上；但如果回滚前已经有用户按新语义提交，
-- 语义上的细微差别是补不回来的。
--
-- 幂等：UPDATE 带 WHERE 只命中新值；约束重建结果一致。

BEGIN;

-- 先放宽到新旧并集，否则下面的 UPDATE 中间态会撞上约束
ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_visitor_type_check;

UPDATE appointments
   SET visitor_type = CASE visitor_type
       WHEN '业主'         THEN 'C端业主'
       WHEN '地产圈'       THEN '地产开发商'
       WHEN '家居圈'       THEN '家居行业经销商'
       WHEN '酒店民宿圈'   THEN '酒店民宿业主'
       WHEN '艺术圈'       THEN '艺术家'
       ELSE visitor_type
       END
 WHERE visitor_type IN ('业主', '地产圈', '家居圈', '酒店民宿圈', '艺术圈');

ALTER TABLE appointments ADD CONSTRAINT appointments_visitor_type_check
  CHECK (visitor_type IN (
    'C端业主', '设计师', '地产开发商', '酒店民宿业主', '家居行业经销商', '艺术家'
  ));

COMMIT;
