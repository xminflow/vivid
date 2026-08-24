-- 回滚 011_shop_shipping_upload.sql —— 删掉 shipping_uploaded_at 列与配套索引。
--
-- 比多数回滚安全：这一列只记录「有没有把物流信息回传给微信」，不是业务凭证。
-- 删了之后重新加回来，所有行都是 NULL，也就是「都还没传过」——重传一次即可，
-- 微信侧对同一笔单重复回传是幂等的（同样的 transaction_id + 同样的运单号）。
--
-- 唯一的代价：删列这一刻，「哪些单已经传成功」的信息就没了。所以如果当时确实有
-- 已发货未回传的单，回滚后会分不清哪些要重传——那就全部重传一遍，不会出错。
--
-- 幂等：DROP ... IF EXISTS，可重复执行。
-- 不动 010 建的任何东西。

BEGIN;

-- 先删索引再删列。其实删列会连带删掉依赖它的索引，写出来是为了顺序显式
DROP INDEX IF EXISTS shop_orders_pending_upload_idx;

ALTER TABLE shop_orders DROP COLUMN IF EXISTS shipping_uploaded_at;

COMMIT;

-- 验证：列没了（应返回 0 行）
--   SELECT column_name FROM information_schema.columns
--    WHERE table_name = 'shop_orders' AND column_name = 'shipping_uploaded_at';
--
-- 其余列都还在（应返回 shipped_at / shipping_type / tracking_no 三行）
--   SELECT column_name FROM information_schema.columns
--    WHERE table_name = 'shop_orders'
--      AND column_name IN ('shipped_at','shipping_type','tracking_no')
--    ORDER BY 1;
