-- 安玺·集 购物车 —— 新建 shop_cart_items 表（发布新代码之前跑）
--
-- 背景：安玺·集从纯陈列进到「能加购」。购物车按用户存在服务端，不放小程序本地缓存——
-- 用户换手机、或在 PC 微信里打开，本地购物车就空了；而且下单时无论如何都要在服务端
-- 校验商品是否还在售、价格是否变过，存本地省不掉这一次校验，只省了一次读。
--
-- 纯新增，不动任何已有表和列：
--   * 旧代码不引用这张表，建完表旧版本服务照常跑，不需要停机
--   * 表是空的时候，购物车接口返回空列表，等于「谁都还没加过购」
--
-- 前置条件：008_shop_catalog.sql 已经跑过（本表外键引用 shop_products）；
--           schema.sql 里的 touch_updated_at() 已存在（users 表那一节建的）。
-- 幂等：CREATE ... IF NOT EXISTS + DROP TRIGGER IF EXISTS，可重复执行。
-- 回滚：跑 009_rollback.sql（直接删表，用户重新加购即可，不丢业务数据）。

BEGIN;

-- 购物车行。只存「谁、要哪件、要几个」，**不存价格**。
--
-- 不存价格快照是刻意的：购物车每次打开都实时回查商品的当前价格与在售状态，
-- 价格以结算那一刻为准。存快照会带来「三天前加购时是 9999，现在是 12999，
-- 按哪个结算」这种没有好答案的问题。
-- 需要固化价格的是**订单**（阶段三建表），那里必须存快照，两件事职责分开。
--
-- 两个外键都 CASCADE：
--   user_id    删用户连购物车一起清，没有留着的意义
--   product_id 商品被硬删时，购物车里的那一行跟着消失。这和订单行的
--              ON DELETE RESTRICT 正好相反——订单是凭证不能动，购物车只是暂存
CREATE TABLE IF NOT EXISTS shop_cart_items (
  id          bigint      PRIMARY KEY,
  user_id     bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  product_id  bigint      NOT NULL REFERENCES shop_products (id) ON DELETE CASCADE,
  quantity    integer     NOT NULL DEFAULT 1
                          CONSTRAINT shop_cart_items_quantity_range
                          CHECK (quantity BETWEEN 1 AND 99),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  -- 同一件商品在一个人的车里只有一行，重复加购是累加数量而不是多出一行。
  -- 靠唯一约束而不是「先查再插」：两次加购几乎同时到达时，查完到插入之间会各自
  -- 认为「还没有」，于是插出两行
  CONSTRAINT shop_cart_items_one_row_per_product UNIQUE (user_id, product_id)
);

-- 按人取车，顺带定下展示顺序：后加的在前
CREATE INDEX IF NOT EXISTS shop_cart_items_user_idx
  ON shop_cart_items (user_id, created_at DESC, id DESC);

-- 商品被删时要靠它找到引用行（外键自己会用，写出来是为了 EXPLAIN 时不困惑）
CREATE INDEX IF NOT EXISTS shop_cart_items_product_idx
  ON shop_cart_items (product_id);

DROP TRIGGER IF EXISTS shop_cart_items_touch_updated_at ON shop_cart_items;
CREATE TRIGGER shop_cart_items_touch_updated_at
  BEFORE UPDATE ON shop_cart_items
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

COMMIT;

-- 验证：
--   表在且列齐（应返回 6 行）
--     SELECT column_name, data_type, is_nullable FROM information_schema.columns
--      WHERE table_name = 'shop_cart_items' ORDER BY ordinal_position;
--   索引与唯一约束都在（应含 user_idx、product_idx、one_row_per_product）
--     SELECT indexname FROM pg_indexes WHERE tablename = 'shop_cart_items' ORDER BY 1;
--   触发器在（应返回 1 行）
--     SELECT tgname FROM pg_trigger
--      WHERE tgrelid = 'shop_cart_items'::regclass AND NOT tgisinternal;
--   同一人同一商品只能有一行、数量必须在 1..99（两条 INSERT 都应报错，随后整个事务回滚）
--     BEGIN;
--     INSERT INTO shop_cart_items (id, user_id, product_id, quantity)
--       SELECT 1, (SELECT id FROM users LIMIT 1), (SELECT id FROM shop_products LIMIT 1), 1;
--     INSERT INTO shop_cart_items (id, user_id, product_id, quantity)
--       SELECT 2, (SELECT id FROM users LIMIT 1), (SELECT id FROM shop_products LIMIT 1), 1;
--       -- 应报 shop_cart_items_one_row_per_product
--     INSERT INTO shop_cart_items (id, user_id, product_id, quantity)
--       SELECT 3, (SELECT id FROM users LIMIT 1), (SELECT id FROM shop_products LIMIT 1), 0;
--       -- 应报 shop_cart_items_quantity_range
--     ROLLBACK;
--   删商品会连带清掉购物车行（不是报错）
--     -- 见 tests/test_shop.py 的 test_deleting_a_product_clears_it_from_carts
