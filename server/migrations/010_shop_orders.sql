-- 安玺·集 下单 —— 新建地址簿、订单、订单行、单号序号、板块设置五张表
-- （发布新代码之前跑）
--
-- 背景：安玺·集从「能加购」进到「能下单」。这一步**还不接支付**，订单建出来停在
-- pending_pay，支付链路是下一阶段的事。先把表建对，因为订单是凭证，
-- 表结构改起来的代价远高于接口。
--
-- 纯新增，不动任何已有表和列：
--   * 旧代码不引用这五张表，建完表旧版本服务照常跑，不需要停机
--   * 表是空的时候，订单接口返回空列表，等于「谁都还没下过单」
--
-- 前置条件：008_shop_catalog.sql 与 009_shop_cart.sql 已经跑过
--           （本迁移的外键引用 shop_products，且沿用 users 与 touch_updated_at()）。
-- 幂等：CREATE ... IF NOT EXISTS + DROP TRIGGER IF EXISTS，可重复执行。
-- 回滚：跑 010_rollback.sql。⚠️ 一旦有真实订单落库，回滚就是**丢业务凭证**，
--       那时应当只回滚代码、保留这些表。

BEGIN;

-- ---------------------------------------------------------------------------
-- 收货地址簿
--
-- 它是「用户当前的地址」，不是「订单用过的地址」。订单里存的是下单那一刻的
-- 快照（见 shop_orders 的六个地址列），所以这张表可以随用户任意改删，
-- 历史订单不受影响。
CREATE TABLE IF NOT EXISTS shop_addresses (
  id          bigint      PRIMARY KEY,
  user_id     bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  receiver    text        NOT NULL
                          CONSTRAINT shop_addresses_receiver_len CHECK (length(receiver) BETWEEN 1 AND 20),
  -- 与 users.phone / appointments.phone 同一条正则，改一处要改全部
  phone       text        NOT NULL CHECK (phone ~ '^1[3-9][0-9]{9}$'),
  province    text        NOT NULL
                          CONSTRAINT shop_addresses_province_len CHECK (length(province) BETWEEN 1 AND 100),
  city        text        NOT NULL
                          CONSTRAINT shop_addresses_city_len CHECK (length(city) BETWEEN 1 AND 100),
  district    text        NOT NULL
                          CONSTRAINT shop_addresses_district_len CHECK (length(district) BETWEEN 1 AND 100),
  detail      text        NOT NULL
                          CONSTRAINT shop_addresses_detail_len CHECK (length(detail) BETWEEN 1 AND 100),
  is_default  boolean     NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS shop_addresses_user_idx
  ON shop_addresses (user_id, created_at DESC, id DESC);

-- 每人至多一个默认地址。部分唯一索引而不是应用层「先清旧的再设新的」：
-- 那两步之间并发进来第二个请求，就会留下两个默认地址，而界面上只显示一个，
-- 表现为「设了没生效」——这类 bug 只在偶发并发下出现，最难查
CREATE UNIQUE INDEX IF NOT EXISTS shop_addresses_one_default_per_user
  ON shop_addresses (user_id) WHERE is_default;

DROP TRIGGER IF EXISTS shop_addresses_touch_updated_at ON shop_addresses;
CREATE TRIGGER shop_addresses_touch_updated_at
  BEFORE UPDATE ON shop_addresses
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- ---------------------------------------------------------------------------
-- 单号序号。单号格式 AX + YYYYMMDD + 6 位序号，如 AX20260810000137。
--
-- 取号是单条原子语句，不需要应用层加锁：
--   INSERT INTO shop_order_seq (day, next) VALUES (CURRENT_DATE, 1)
--     ON CONFLICT (day) DO UPDATE SET next = shop_order_seq.next + 1
--     RETURNING next;
-- 冲突分支里的 UPDATE 会持有该行的行锁直到事务结束，并发下自然串行。
--
-- 为什么不用 sequence：sequence 不回滚（下单失败会咬掉号，单号出现空洞），
-- 也不好按天归零。为什么不用「查 max(order_no) + 1」：那要全表扫且有竞态。
--
-- 该单号同时作为微信支付的 out_trade_no（微信要求 6–32 位、商户内唯一，16 位正好）。
CREATE TABLE IF NOT EXISTS shop_order_seq (
  day   date    PRIMARY KEY,
  next  integer NOT NULL
                CONSTRAINT shop_order_seq_next_range CHECK (next BETWEEN 1 AND 999999)
);

-- ---------------------------------------------------------------------------
-- 订单。
--
-- 状态机（旁支不可逆）：
--   pending_pay ──支付成功──> pending_ship ──发货──> pending_receive ──收货──> completed
--        │                                                              ↑
--        ├──超时/用户取消──> closed                          （10 天自动确认）
--        └──（已支付后由超管退款）──────> refunded
--
-- 六个地址列是**快照**，不存 address_id 引用：用户改了地址簿或删了那条地址，
-- 都不能改变已经下过的单。这是凭证类数据的一贯做法，和订单行存 title/price 快照同理。
--
-- 金额只存 total_cents 一个总额，不存运费/优惠等分项——本期没有这些概念，
-- 提前留列只会让人以为它们有意义。
CREATE TABLE IF NOT EXISTS shop_orders (
  id                bigint      PRIMARY KEY,
  -- 展示给用户、也是微信支付的 out_trade_no。唯一索引是并发取号的最后一道防线
  order_no          text        NOT NULL UNIQUE
                                CONSTRAINT shop_orders_no_format CHECK (order_no ~ '^AX[0-9]{14}$'),
  user_id           bigint      NOT NULL REFERENCES users (id),
  status            text        NOT NULL DEFAULT 'pending_pay'
                                CHECK (status IN ('pending_pay', 'pending_ship', 'pending_receive',
                                                  'completed', 'closed', 'refunded')),
  -- 这一单是从购物车结算的还是详情页「立即购买」的。
  -- 它只有一个用途：**支付成功时决定要不要把这些商品从购物车里清掉**。
  -- 不存的话，到支付回调那一刻就无从判断，只能「在车里就顺手清掉」——
  -- 那会让详情页直接购买莫名其妙地清掉用户车里的同款
  source            text        NOT NULL DEFAULT 'cart'
                                CHECK (source IN ('cart', 'direct')),
  total_cents       integer     NOT NULL
                                CONSTRAINT shop_orders_total_positive CHECK (total_cents > 0),

  -- 地址快照
  receiver          text        NOT NULL,
  phone             text        NOT NULL,
  province          text        NOT NULL,
  city              text        NOT NULL,
  district          text        NOT NULL,
  detail            text        NOT NULL,

  -- 支付。transaction_id 是微信支付订单号，回调与主动查单都靠它做幂等
  transaction_id    text,
  paid_at           timestamptz,
  -- 主动查单与超时扫描共用的节流字段：上次向微信查这笔单的时刻
  last_query_at     timestamptz,

  -- 发货。三档发货方式都是刚需：卖家具走专线或自送时没有运单号
  shipping_type     text        CHECK (shipping_type IN ('express', 'local', 'none')),
  shipping_company  text,
  tracking_no       text,
  shipped_at        timestamptz,

  received_at       timestamptz,

  closed_at         timestamptz,
  close_reason      text        CHECK (close_reason IN ('timeout', 'user_cancel')),

  refunded_at       timestamptz,
  refund_reason     text,
  refund_id         text,

  -- 运营在后台记的备注，用户看不到
  remark            text        NOT NULL DEFAULT '',

  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),

  -- 走快递就必须有运单号，另外两档必须没有。没有这条约束的话，
  -- 「选了快递但运单号为空」会一路流到微信的发货信息录入接口才报错
  CONSTRAINT shop_orders_express_needs_tracking CHECK (
    shipping_type IS DISTINCT FROM 'express' OR (tracking_no IS NOT NULL AND tracking_no <> '')
  )
);

-- 「我的订单」按人倒序翻页
CREATE INDEX IF NOT EXISTS shop_orders_user_idx
  ON shop_orders (user_id, created_at DESC, id DESC);

-- 后台按状态筛 + 定时任务扫 pending_pay / pending_receive
CREATE INDEX IF NOT EXISTS shop_orders_status_idx
  ON shop_orders (status, created_at DESC, id DESC);

-- 微信支付订单号唯一。这是回调幂等在**数据库层**的第二道防线：
-- 应用层已经用 SELECT ... FOR UPDATE 锁行判重，但回调可能被微信重投多次、
-- 也可能与主动查单同时到达，多进程下只有唯一索引挡得住
CREATE UNIQUE INDEX IF NOT EXISTS shop_orders_transaction_uniq
  ON shop_orders (transaction_id) WHERE transaction_id IS NOT NULL;

DROP TRIGGER IF EXISTS shop_orders_touch_updated_at ON shop_orders;
CREATE TRIGGER shop_orders_touch_updated_at
  BEFORE UPDATE ON shop_orders
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- ---------------------------------------------------------------------------
-- 订单行。金额与标题、封面全部是**下单那一刻的快照**，
-- 任何场景（列表、详情、退款、对账）都不回查 shop_products 的当前值。
--
-- 两个外键方向相反，各有各的道理：
--   order_id    ON DELETE CASCADE —— 订单没了行也没有意义（但订单本身不提供删除）
--   product_id  ON DELETE RESTRICT —— **卖过的商品永远删不掉，只能下架**。
--               这条约束就是「商品不可删」的执行点，由数据库强制而不是靠应用层记得检查。
--               后台的删除接口会把外键违约翻译成 409 并列出引用它的订单号。
CREATE TABLE IF NOT EXISTS shop_order_items (
  id                    bigint      PRIMARY KEY,
  order_id              bigint      NOT NULL REFERENCES shop_orders (id) ON DELETE CASCADE,
  product_id            bigint      NOT NULL REFERENCES shop_products (id) ON DELETE RESTRICT,
  title_snapshot        text        NOT NULL,
  price_cents_snapshot  integer     NOT NULL
                                    CONSTRAINT shop_order_items_price_positive
                                    CHECK (price_cents_snapshot > 0),
  -- 封面 COS key。商品图后来被换掉也不影响历史订单的展示
  image_snapshot        text        NOT NULL DEFAULT '',
  quantity              integer     NOT NULL
                                    CONSTRAINT shop_order_items_quantity_range
                                    CHECK (quantity BETWEEN 1 AND 99),
  created_at            timestamptz NOT NULL DEFAULT now()
);

-- 上面的 CREATE TABLE 对**已经建过这张表**的库不生效（比如先跑了不带 source 的
-- 那一版）。带默认值加列在 PG 11+ 不重写全表，存量行直接读到 'cart'——
-- 那时还没有 direct 单，这个默认值对历史数据是正确的
ALTER TABLE shop_orders ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'cart';

-- 约束得单独补，Postgres 没有 ADD CONSTRAINT IF NOT EXISTS。
-- 已经加过就吞掉 duplicate_object，让本文件保持可重复执行
DO $$
BEGIN
  ALTER TABLE shop_orders ADD CONSTRAINT shop_orders_source_check
    CHECK (source IN ('cart', 'direct'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS shop_order_items_order_idx
  ON shop_order_items (order_id, id);

-- 后台删商品前要靠它查出「哪些订单引用了这件商品」
CREATE INDEX IF NOT EXISTS shop_order_items_product_idx
  ON shop_order_items (product_id);

-- ---------------------------------------------------------------------------
-- 板块设置。单行表，id 恒为 1。
--
-- 用「一张表一行」而不是 key-value：目前只有客服二维码一项，
-- 加列比加行更容易看出有哪些设置，也免去每次读都要判空。
CREATE TABLE IF NOT EXISTS shop_settings (
  id              smallint    PRIMARY KEY CHECK (id = 1),
  -- 客服二维码的 COS key。空串表示还没配，小程序据此隐藏「联系客服」入口
  service_qr_key  text        NOT NULL DEFAULT '',
  updated_at      timestamptz NOT NULL DEFAULT now()
);

-- 先塞好那一行，接口里就只有 UPDATE 一条路径，不用处理「行还不存在」
INSERT INTO shop_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

DROP TRIGGER IF EXISTS shop_settings_touch_updated_at ON shop_settings;
CREATE TRIGGER shop_settings_touch_updated_at
  BEFORE UPDATE ON shop_settings
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

COMMIT;

-- 验证：
--   五张表都在（应返回 5 行）
--     SELECT tablename FROM pg_tables
--      WHERE schemaname = 'public'
--        AND tablename IN ('shop_addresses','shop_orders','shop_order_items',
--                          'shop_order_seq','shop_settings')
--      ORDER BY 1;
--
--   设置表已经有那一行（应返回 1）
--     SELECT count(*) FROM shop_settings WHERE id = 1;
--
--   每人只能有一个默认地址（第二条 INSERT 应报 shop_addresses_one_default_per_user）
--     BEGIN;
--     INSERT INTO shop_addresses (id, user_id, receiver, phone, province, city, district, detail, is_default)
--       SELECT 1, (SELECT id FROM users LIMIT 1), '张三', '13612345678', '上海市','上海市','静安区','某路 1 号', true;
--     INSERT INTO shop_addresses (id, user_id, receiver, phone, province, city, district, detail, is_default)
--       SELECT 2, (SELECT id FROM users LIMIT 1), '李四', '13612345679', '上海市','上海市','静安区','某路 2 号', true;
--     ROLLBACK;
--
--   取号是原子的、按天归零（连跑两次应得 1 再得 2）
--     INSERT INTO shop_order_seq (day, next) VALUES (CURRENT_DATE, 1)
--       ON CONFLICT (day) DO UPDATE SET next = shop_order_seq.next + 1 RETURNING next;
--
--   单号格式受约束（应报 shop_orders_no_format）
--     BEGIN;
--     INSERT INTO shop_orders (id, order_no, user_id, total_cents, receiver, phone,
--                              province, city, district, detail)
--       SELECT 1, 'BAD-NO', (SELECT id FROM users LIMIT 1), 100, '张三','13612345678',
--              '上海市','上海市','静安区','某路 1 号';
--     ROLLBACK;
--
--   选了快递却没运单号会被挡（应报 shop_orders_express_needs_tracking）
--     BEGIN;
--     INSERT INTO shop_orders (id, order_no, user_id, total_cents, receiver, phone,
--                              province, city, district, detail, shipping_type)
--       SELECT 1, 'AX20260812000001', (SELECT id FROM users LIMIT 1), 100, '张三','13612345678',
--              '上海市','上海市','静安区','某路 1 号', 'express';
--     ROLLBACK;
--
--   卖过的商品删不掉（第二条 DELETE 应报外键违约 shop_order_items_product_id_fkey）
--     -- 见 tests/test_shop_orders.py
