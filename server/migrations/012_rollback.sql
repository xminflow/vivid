-- 回滚 012_payment_hardening.sql
--
-- ⚠️ 这个回滚**不是无条件安全的**，有一步会因为存量数据而失败，这是有意的。
--
-- ## 收紧订单号约束这一步可能失败
--
-- 如果回滚之前开发环境已经用新前缀（AD）发过单，把 CHECK 收回 '^AX[0-9]{14}$'
-- 会因为存量行不满足而报错，整个事务回滚——**这正是想要的**：宁可回滚失败，
-- 也不能让约束和数据对不上（那种库之后任何一次 UPDATE 都可能踩雷）。
--
-- 真要在这种情况下回滚，先决定那些 AD 单怎么办（开发库直接删是可以的）：
--   DELETE FROM shop_order_items WHERE order_id IN
--     (SELECT id FROM shop_orders WHERE order_no !~ '^AX[0-9]{14}$');
--   DELETE FROM shop_orders WHERE order_no !~ '^AX[0-9]{14}$';
-- 生产库的前缀没变过（一直是 AX），不会遇到这一步。
--
-- ## 其余几步是安全的
--
-- sweep_attempts / last_pay_at 只是节流与失败计数，不是业务凭证，删了重建
-- 全是默认值，语义上等于「还没扫过、还没下过单」，对存量数据是正确的。
--
-- close_reason 收回两个取值前，要先把 never_submitted 的行改掉，否则同样报错。
-- 那些单本来就是关闭状态，归到 timeout 不改变任何业务含义。
--
-- payment_anomalies **整张表会被删掉**。这是资金异常的台账，删之前先导出：
--   \copy (SELECT * FROM payment_anomalies) TO 'payment_anomalies.csv' CSV HEADER
--
-- 幂等：DROP ... IF EXISTS 可重复执行；两处 ADD CONSTRAINT 重复跑会因为同名
-- 约束已存在而报错，所以每一处都先 DROP。

BEGIN;

-- 先把新取值改掉，否则下面收紧 CHECK 会失败
UPDATE shop_orders SET close_reason = 'timeout' WHERE close_reason = 'never_submitted';

ALTER TABLE shop_orders DROP CONSTRAINT IF EXISTS shop_orders_close_reason_check;
ALTER TABLE shop_orders
  ADD CONSTRAINT shop_orders_close_reason_check
  CHECK (close_reason IN ('timeout', 'user_cancel'));

-- 这一步在开发库上可能失败，见文件开头
ALTER TABLE shop_orders DROP CONSTRAINT IF EXISTS shop_orders_no_format;
ALTER TABLE shop_orders
  ADD CONSTRAINT shop_orders_no_format CHECK (order_no ~ '^AX[0-9]{14}$');

ALTER TABLE shop_orders
  DROP COLUMN IF EXISTS sweep_attempts,
  DROP COLUMN IF EXISTS last_pay_at;

DROP INDEX IF EXISTS payment_anomalies_open_idx;
DROP INDEX IF EXISTS payment_anomalies_order_no_idx;
DROP TABLE IF EXISTS payment_anomalies;

COMMIT;

-- 验证：
--   两个新列没了（应返回 0 行）
--     SELECT column_name FROM information_schema.columns
--      WHERE table_name = 'shop_orders'
--        AND column_name IN ('sweep_attempts', 'last_pay_at');
--
--   台账表没了（应返回 0 行）
--     SELECT tablename FROM pg_tables WHERE tablename = 'payment_anomalies';
--
--   单号约束收回去了（定义里是 ^AX）
--     SELECT pg_get_constraintdef(oid) FROM pg_constraint
--      WHERE conname = 'shop_orders_no_format';
