-- 「来者身份」选项改版 —— 第 1 步：放宽约束（发布新代码之前跑）
--
-- 新旧值对照：
--   C端业主        → 业主
--   设计师         → 设计师（不变）
--   地产开发商     → 地产圈
--   家居行业经销商 → 家居圈
--   酒店民宿业主   → 酒店民宿圈
--   艺术家         → 艺术圈
--
-- 为什么要分两步：CHECK 约束和应用代码不可能同一瞬间切换。
--   直接换成只认新值 → 还在跑的旧代码（小程序旧版本、未刷新的官网页面）提交旧值会 500；
--   先发代码再换约束 → 新代码提交新值，旧约束不认，同样 500。
-- 所以中间留一个「新旧都认」的过渡态，两头的代码在任何时刻都能写库。
--
-- 幂等：DROP ... IF EXISTS 之后重建，重复执行结果一致。
-- 回滚：直接跑 001_rollback.sql（恢复成只认旧值）。
-- 执行完这一步后再发布新代码，然后跑 002_visitor_type_narrow.sql。

BEGIN;

ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_visitor_type_check;

ALTER TABLE appointments ADD CONSTRAINT appointments_visitor_type_check
  CHECK (visitor_type IN (
    -- 新值
    '业主', '设计师', '地产圈', '家居圈', '酒店民宿圈', '艺术圈',
    -- 旧值：过渡期继续放行，由 002 收紧掉
    'C端业主', '地产开发商', '家居行业经销商', '酒店民宿业主', '艺术家'
  ));

COMMIT;

-- 验证：约束里应同时出现新旧两组值
-- SELECT pg_get_constraintdef(oid) FROM pg_constraint
--  WHERE conname = 'appointments_visitor_type_check';
