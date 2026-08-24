-- 008 的回滚：删掉 shop_products 与 shop_categories 两张表。
--
-- 什么时候用：安玺·集板块整体下线，或要退回不认识这两张表的旧版本服务。
--
-- ⚠️ 会丢掉后台录入的**全部商品与分类**（标题、价格、简介、参数、图片顺序）。
-- 图片本身还在 COS 的 static/shop/ 前缀下，不会被删，但「哪张图属于哪个商品、
-- 排第几」这层信息只存在于这两张表里，删了就得对着 COS 一张张认。要留底的话，
-- 删表前先导出：
--   \copy (SELECT id, name, sort_order, status FROM shop_categories ORDER BY id)
--     TO 'shop_categories_backup.csv' CSV HEADER
--   \copy (SELECT id, category_id, title, summary, price_cents, images, detail_images,
--                 params, status, sort_order FROM shop_products ORDER BY id)
--     TO 'shop_products_backup.csv' CSV HEADER
--
-- 退回旧版本服务本身不需要跑这个：旧代码不引用这两张表，留着也不影响。
-- 只有确认不再需要这份数据时才执行。
--
-- ⚠️ 阶段二之后不要直接跑这个：届时 shop_order_items 会以 ON DELETE RESTRICT
-- 引用 shop_products，删表会被外键挡住——那说明已经有真实订单了，
-- 此时正确的做法是先回滚阶段二的迁移。
--
-- 顺序：先删 shop_products 再删 shop_categories，因为前者外键引用后者。
-- 幂等：DROP ... IF EXISTS，可重复执行。

BEGIN;

-- 表一删，它身上的索引和触发器跟着没了，不用单独 DROP
DROP TABLE IF EXISTS shop_products;
DROP TABLE IF EXISTS shop_categories;

COMMIT;

-- 验证：应返回 0 行
--   SELECT tablename FROM pg_tables WHERE tablename IN ('shop_products', 'shop_categories');
