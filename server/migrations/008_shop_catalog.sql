-- 安玺·集 商品目录 —— 新建 shop_categories / shop_products 两张表（发布新代码之前跑）
--
-- 背景：antony-casa 小程序新增「安玺·集」购物板块，需要运营在后台上架商品。
-- 本次只建**目录**相关的两张表（分类、商品）；交易链路（购物车、地址、订单、订单行）
-- 属于阶段二，留给后续迁移，不在这里预建空表。
--
-- 纯新增，不动任何已有表和列：
--   * 旧代码不引用这两张表，建完表旧版本服务照常跑，不需要停机
--   * 表里没有数据时，小程序端 /api/shop/products 返回空列表，
--     而该板块的 tab 入口要等新版本小程序发布才出现，所以这一步没有任何用户可见变化
--
-- 前置条件：schema.sql 里的 touch_updated_at() 已存在（users 表那一节建的）。
-- 幂等：CREATE ... IF NOT EXISTS + DROP TRIGGER IF EXISTS，可重复执行。
-- 回滚：跑 008_rollback.sql（直接删两张表，图还在 COS 上，重新配一遍即可）。

BEGIN;

-- 商品分类。单层，商品必属其一。
--
-- 只能停用、不能删除：停用会把该分类下所有在售商品一并改写为下架，
-- 且重新启用**不会**自动恢复（需要后台点「批量上架」）。
-- 这样做是为了让 shop_products.status 保持「商品能否被购买」的唯一判据——
-- 若分类状态能隐式改变可购性，后台会显示「在售」而用户买不到。
CREATE TABLE IF NOT EXISTS shop_categories (
  id          bigint      PRIMARY KEY,
  name        text        NOT NULL UNIQUE
                          CONSTRAINT shop_categories_name_len CHECK (length(name) BETWEEN 1 AND 20),
  sort_order  integer     NOT NULL DEFAULT 0,
  status      text        NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active', 'disabled')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- 小程序端按 sort_order 倒序取启用中的分类
CREATE INDEX IF NOT EXISTS shop_categories_listing_idx
  ON shop_categories (status, sort_order DESC, id);

DROP TRIGGER IF EXISTS shop_categories_touch_updated_at ON shop_categories;
CREATE TRIGGER shop_categories_touch_updated_at
  BEFORE UPDATE ON shop_categories
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- 商品。无 SKU、无库存：一个商品一个价格，status 是能否被购买的唯一判据。
--
-- category_id 不写 ON DELETE：默认的 NO ACTION 正好让「分类下还有商品就删不掉」
-- 由数据库兜底。接口层本来就不提供删除分类的入口，这是第二道防线。
--
-- 三个 jsonb 列都是**有序数组**，顺序即展示顺序：
--   images        COS key 数组，[0] 兼作列表页封面
--   detail_images COS key 数组，详情正文按序渲染
--   params        [{"name": "材质", "value": "实木"}]，展示型参数，不影响价格与可购性
-- 数量上限写进 CHECK，避免应用层漏校验就把几百张图塞进来。
-- 单条 key 的前缀与长度校验放在 models.py（check_static_key），SQL 里不重复。
CREATE TABLE IF NOT EXISTS shop_products (
  id             bigint      PRIMARY KEY,
  category_id    bigint      NOT NULL REFERENCES shop_categories (id),
  title          text        NOT NULL
                             CONSTRAINT shop_products_title_len CHECK (length(title) BETWEEN 1 AND 60),
  summary        text        NOT NULL DEFAULT ''
                             CONSTRAINT shop_products_summary_len CHECK (length(summary) <= 500),
  price_cents    integer     NOT NULL
                             CONSTRAINT shop_products_price_positive CHECK (price_cents > 0),
  images         jsonb       NOT NULL DEFAULT '[]'::jsonb
                             CONSTRAINT shop_products_images_shape
                             CHECK (jsonb_typeof(images) = 'array' AND jsonb_array_length(images) <= 10),
  detail_images  jsonb       NOT NULL DEFAULT '[]'::jsonb
                             CONSTRAINT shop_products_detail_images_shape
                             CHECK (jsonb_typeof(detail_images) = 'array' AND jsonb_array_length(detail_images) <= 20),
  params         jsonb       NOT NULL DEFAULT '[]'::jsonb
                             CONSTRAINT shop_products_params_shape
                             CHECK (jsonb_typeof(params) = 'array' AND jsonb_array_length(params) <= 20),
  status         text        NOT NULL DEFAULT 'off'
                             CHECK (status IN ('active', 'off')),
  sort_order     integer     NOT NULL DEFAULT 0,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

-- 小程序端列表：先按状态过滤，再按 sort_order 倒序、同序号按新旧
CREATE INDEX IF NOT EXISTS shop_products_listing_idx
  ON shop_products (status, sort_order DESC, created_at DESC, id);

-- 后台按分类筛选，以及分类停用时批量改写该分类下的商品
CREATE INDEX IF NOT EXISTS shop_products_category_idx
  ON shop_products (category_id);

DROP TRIGGER IF EXISTS shop_products_touch_updated_at ON shop_products;
CREATE TRIGGER shop_products_touch_updated_at
  BEFORE UPDATE ON shop_products
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

COMMIT;

-- 验证：
--   两张表都在且列齐（shop_categories 应 6 行，shop_products 应 12 行）
--     SELECT table_name, count(*) FROM information_schema.columns
--      WHERE table_name IN ('shop_categories', 'shop_products') GROUP BY table_name;
--   三个索引都在（listing ×2 + category ×1）
--     SELECT indexname FROM pg_indexes
--      WHERE tablename IN ('shop_categories', 'shop_products') ORDER BY indexname;
--   两个触发器都在（应返回 2 行）
--     SELECT tgrelid::regclass, tgname FROM pg_trigger
--      WHERE tgrelid IN ('shop_categories'::regclass, 'shop_products'::regclass) AND NOT tgisinternal;
--   商品默认下架、价格必须为正、图集上限生效
--     BEGIN;
--     INSERT INTO shop_categories (id, name) VALUES (1, '验证用分类');
--     INSERT INTO shop_products (id, category_id, title, price_cents) VALUES (1, 1, '验证用商品', 100);
--     SELECT status FROM shop_products WHERE id = 1;              -- 应为 off
--     INSERT INTO shop_products (id, category_id, title, price_cents)
--       VALUES (2, 1, '零元购', 0);                                -- 应报 shop_products_price_positive
--     ROLLBACK;
--   分类下有商品时删不掉（应报外键违约，随后整个事务回滚）
--     BEGIN;
--     INSERT INTO shop_categories (id, name) VALUES (2, '验证用分类二');
--     INSERT INTO shop_products (id, category_id, title, price_cents) VALUES (3, 2, '占位商品', 100);
--     DELETE FROM shop_categories WHERE id = 2;                   -- 应报 violates foreign key constraint
--     ROLLBACK;
