-- 安玺·集 发货 —— 给 shop_orders 加一列，记录「物流信息有没有回传给微信」
-- （发布新代码之前跑）
--
-- 背景：微信对小程序实物交易有硬要求——支付成功后要在时限内把物流信息回传给
-- **小程序开放接口**（不是微信支付接口），否则订单被判发货延迟，连续三天不整改
-- 会触发支付风险提示直到暂停交易。
--
-- 为什么单独一列而不是复用 shipped_at：这是两件事。
--   shipped_at            运营在我们后台点了「发货」的时刻
--   shipping_uploaded_at  我们成功把这条信息告诉微信的时刻
-- 回传是一次网络调用，会失败（access_token 过期、物流类型枚举填错、微信侧限流）。
-- 两者混成一个的话，回传失败时只有两种选择：要么假装发货成功（于是违规在
-- 无人知晓的情况下累计），要么把发货整个回滚（可运营明明已经把货交给快递了）。
-- 分开之后，「已发货但没回传成功」是一个可以被查询、被重试的明确状态：
--   SELECT order_no FROM shop_orders
--    WHERE shipped_at IS NOT NULL AND shipping_uploaded_at IS NULL;
--
-- 纯新增一列，不动任何已有数据：
--   * 旧代码不引用它，建完列旧版本服务照常跑，不需要停机
--   * 存量行读到 NULL，语义正是「还没回传过」——对历史数据是正确的
--     （在此之前根本没有发货功能，所以不存在「其实传过但记成 NULL」的行）
--
-- 前置条件：010_shop_orders.sql 已经跑过。
-- 幂等：ADD COLUMN IF NOT EXISTS，可重复执行。
-- 回滚：跑 011_rollback.sql（删列即可，不丢业务数据——只丢「传没传过」的记录，
--       重传一次是幂等的，微信侧同一笔单重复回传不会出错）。

BEGIN;

ALTER TABLE shop_orders
  ADD COLUMN IF NOT EXISTS shipping_uploaded_at timestamptz;

-- 找「已发货但还没回传成功」的单。运营看不到这个状态，是给定时重试和人工排查用的。
-- 部分索引而不是全表索引：绝大多数订单要么没发货、要么已经传成功，
-- 真正落在这个索引里的只有出问题的那几笔
CREATE INDEX IF NOT EXISTS shop_orders_pending_upload_idx
  ON shop_orders (shipped_at)
  WHERE shipped_at IS NOT NULL AND shipping_uploaded_at IS NULL;

COMMIT;

-- 验证：
--   列在（应返回 1 行，data_type = timestamp with time zone）
--     SELECT column_name, data_type, is_nullable FROM information_schema.columns
--      WHERE table_name = 'shop_orders' AND column_name = 'shipping_uploaded_at';
--
--   索引在（应返回 1 行）
--     SELECT indexname FROM pg_indexes
--      WHERE tablename = 'shop_orders' AND indexname = 'shop_orders_pending_upload_idx';
--
--   存量行都是 NULL（应返回 0）
--     SELECT count(*) FROM shop_orders WHERE shipping_uploaded_at IS NOT NULL;
--
--   待重传的单（正常应为 0；不为 0 就是有发货没告诉微信，要尽快处理）
--     SELECT order_no, shipped_at FROM shop_orders
--      WHERE shipped_at IS NOT NULL AND shipping_uploaded_at IS NULL
--      ORDER BY shipped_at;
