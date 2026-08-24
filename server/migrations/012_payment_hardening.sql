-- 安玺·集 支付加固 —— 订单号环境前缀、扫描失败计数、支付异常台账
-- （发布新代码之前跑）
--
-- 一次迁移做三件事，因为它们都要在同一次发版里生效，分成三个文件只会让
-- 「跑到一半发新代码」变成一种可能的状态。
--
-- ## 一、订单号前缀放宽到两位字母
--
-- 微信支付**没有沙箱**：开发环境与生产环境用的是同一个商户号、同一套密钥
-- （见 deploy/dev/.env.example 的说明）。而订单号的序号取自 shop_order_seq 表，
-- 两个环境是**两个独立的库**（antony_casa / antony_casa_dev），各自从 1 开始发号，
-- 于是同一天必然发出同名的 out_trade_no——而 out_trade_no 在**商户维度**必须唯一。
--
-- 撞号之后有三条破坏路径，都不需要任何人操作失误：
--   1. 关单串台  超时扫描调 close_order(order_no)，关掉的是商户下那个号，
--                也就是**另一个环境**里正被客户支付的那一单
--   2. 退款串台  每日对账下载的是**商户级**账单，按单号 UPDATE，
--                开发环境退一笔测试单，生产上同号的单会被自动置成已退款
--   3. 下单串台  用已被对方占用的号下单，微信要么报单号重复，
--                要么返回绑定到对方那笔订单的 prepay
--
-- 所以订单号的前两位改成**按环境配置**（ORDER_NO_PREFIX，生产 AX、开发 AD），
-- CHECK 相应放宽。靠「开发环境不配支付」的约定挡不住这件事：.env 里填了就生效，
-- 而那正是开发环境要验支付时必须做的。
--
-- 存量数据全部是 AX 开头，天然满足新约束，不需要回填。
--
-- ⚠️ 开发库里**已经存在的 AX 单**仍然与生产撞号。新代码的扫描与对账都只认本环境
-- 前缀，所以它们不会再去动生产那边的号，但它们自己也不会再被超时关单扫到。
-- 在开发库上跑一次下面这句把它们收掉（生产库不要跑，生产的前缀没变）：
--   UPDATE shop_orders SET status = 'closed', closed_at = now(),
--          close_reason = 'never_submitted'
--    WHERE status = 'pending_pay' AND order_no LIKE 'AX%';
--
-- ## 二、sweep_attempts —— 超时关单扫描的失败计数
--
-- 扫描的取数是 `WHERE status='pending_pay' AND created_at < deadline
-- ORDER BY created_at LIMIT 50`，而有两类单会**永久**留在这个条件里：
--   * 从没成功提交到微信的单（下单时微信抖了一下，订单仍按设计保留为待付款）
--     —— 查单返回 ORDERNOTEXIST，老代码 continue 跳过，下一轮又选中它
--   * 金额与订单不符、被拒绝迁移状态的单 —— 同样永远停在待付款
-- 它们又恰恰是最老的，ORDER BY created_at 保证每轮都优先选中。
-- **攒够 50 笔，超时关单就彻底停止工作**，之后所有超时单永远停在待付款。
--
-- 计数满 SWEEP_MAX_ATTEMPTS（app/shop_pay.py）之后不再选中，人工排查用：
--   SELECT order_no, sweep_attempts, created_at FROM shop_orders
--    WHERE status = 'pending_pay' AND sweep_attempts >= 5 ORDER BY created_at;
--
-- ## 三、last_pay_at —— 重新拉起支付的节流
--
-- POST /orders/{id}/pay 每次都真的向微信下一次单。老代码没有任何节流，
-- 循环调用等于无限消耗微信的下单频率配额，还各占一条数据库连接（池只有 5 条）。
--
-- ## 四、payment_anomalies —— 收了钱但对不上单的台账
--
-- 回调金额与订单不符、或单号根本不存在时，老代码只记一行 error 日志就返回
-- SUCCESS（让微信别再重投）。钱确实收了，而唯一的线索是一行没人会主动去翻的日志。
-- 这类事件必须落库、可查询、可标记已处理。
--
-- 前置条件：010_shop_orders.sql、011_shop_shipping_upload.sql 已经跑过。
-- 幂等：全部 IF EXISTS / IF NOT EXISTS，可重复执行。
-- 回滚：跑 012_rollback.sql。

BEGIN;

-- ---------------------------------------------------------------- 一、订单号前缀
-- 先删后加。CHECK 加上时会校验存量行，存量全是 AX 开头，必然通过
ALTER TABLE shop_orders DROP CONSTRAINT IF EXISTS shop_orders_no_format;
ALTER TABLE shop_orders
  ADD CONSTRAINT shop_orders_no_format CHECK (order_no ~ '^[A-Z]{2}[0-9]{14}$');

-- ------------------------------------------------- 二、三、扫描计数与下单节流
ALTER TABLE shop_orders
  ADD COLUMN IF NOT EXISTS sweep_attempts integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_pay_at    timestamptz;

-- 微信侧根本不存在这笔单（下单时就没提交成功），本地直接关掉。
-- 与 timeout 分开：timeout 是「用户没在时限内付」，这个是「压根没送到微信」，
-- 两者的排查方向完全不同
ALTER TABLE shop_orders DROP CONSTRAINT IF EXISTS shop_orders_close_reason_check;
ALTER TABLE shop_orders
  ADD CONSTRAINT shop_orders_close_reason_check
  CHECK (close_reason IN ('timeout', 'user_cancel', 'never_submitted'));

-- ---------------------------------------------------------- 四、支付异常台账
CREATE TABLE IF NOT EXISTS payment_anomalies (
  id              bigint      PRIMARY KEY,
  -- amount_mismatch          微信说付了 X，订单是 Y —— 要么建单时算错，要么有人改了金额
  -- order_not_found          收到一笔支付成功，但这个单号在库里不存在
  -- missing_transaction_id   微信说付成功却没给支付单号，无法做幂等，只能挂起
  kind            text        NOT NULL
                              CHECK (kind IN ('amount_mismatch', 'order_not_found',
                                              'missing_transaction_id')),
  -- 哪条通道发现的。三个入口都会写，排查时要知道是回调、查单还是扫描发现的
  source          text        NOT NULL CHECK (source IN ('notify', 'sync_pay', 'sweep')),

  -- 单号一定有（异常正是围绕它发生的）；order_id 在「单号不存在」时为空
  order_no        text        NOT NULL,
  order_id        bigint,
  transaction_id  text        NOT NULL DEFAULT '',
  -- 分。order_not_found 时 expected_cents 为空——我们没有那笔订单
  paid_cents      integer,
  expected_cents  integer,

  -- 处理记录。不删行：这是资金异常的台账，处理完也要留着
  resolved_at     timestamptz,
  resolved_by     text        NOT NULL DEFAULT '',
  resolve_note    text        NOT NULL DEFAULT '',

  created_at      timestamptz NOT NULL DEFAULT now()
);

-- 后台列表默认只看未处理的，按时间倒序。部分索引：处理完的行不该拖慢这个查询
CREATE INDEX IF NOT EXISTS payment_anomalies_open_idx
  ON payment_anomalies (created_at DESC, id DESC) WHERE resolved_at IS NULL;

-- 按单号回查。排查时手里往往只有一个订单号
CREATE INDEX IF NOT EXISTS payment_anomalies_order_no_idx
  ON payment_anomalies (order_no);

COMMIT;

-- 验证：
--   新的单号约束生效（应返回一行，定义里是 [A-Z]{2}）
--     SELECT pg_get_constraintdef(oid) FROM pg_constraint
--      WHERE conname = 'shop_orders_no_format';
--
--   存量订单号全部满足新约束（应返回 0）
--     SELECT count(*) FROM shop_orders WHERE order_no !~ '^[A-Z]{2}[0-9]{14}$';
--
--   两个新列在，且存量行取到默认值（sweep_attempts 应全为 0，last_pay_at 全为 NULL）
--     SELECT count(*) AS 总数,
--            count(*) FILTER (WHERE sweep_attempts = 0)  AS 计数为零,
--            count(*) FILTER (WHERE last_pay_at IS NULL) AS 未下过单
--       FROM shop_orders;
--
--   台账表与两个索引在（应返回 2 行）
--     SELECT indexname FROM pg_indexes WHERE tablename = 'payment_anomalies' ORDER BY 1;
--
--   close_reason 允许新值（应返回一行，定义里含 never_submitted）
--     SELECT pg_get_constraintdef(oid) FROM pg_constraint
--      WHERE conname = 'shop_orders_close_reason_check';
--
-- 发布后要定期看的两句：
--   未处理的资金异常（正常应为 0，不为 0 就是有钱对不上单）
--     SELECT kind, source, order_no, paid_cents, expected_cents, created_at
--       FROM payment_anomalies WHERE resolved_at IS NULL ORDER BY created_at DESC;
--
--   扫描已经放弃的单（正常应为 0）
--     SELECT order_no, sweep_attempts, created_at FROM shop_orders
--      WHERE status = 'pending_pay' AND sweep_attempts >= 5 ORDER BY created_at;
