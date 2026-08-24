-- 回滚 010_shop_orders.sql —— 删掉地址簿、订单、订单行、单号序号、板块设置五张表。
--
-- ⚠️ 只在「表建好但还没有真实订单」这个窗口里可用。一旦有用户下过单，
-- 这份脚本删掉的就是**业务凭证**（谁买了什么、付了多少、发到哪），
-- 那时正确的做法是只回滚代码、保留这些表——旧代码不引用它们，留着无害。
--
-- 跑之前先确认订单表是空的：
--   SELECT count(*) AS orders, (SELECT count(*) FROM shop_order_items) AS items FROM shop_orders;
-- 两个都是 0 才继续。不是 0 就别跑，先把数据导出来。
--
-- 幂等：DROP ... IF EXISTS，可重复执行。
-- 不动 008/009 建的 shop_categories / shop_products / shop_cart_items。

BEGIN;

-- 顺序要紧：先删引用方，再删被引用方。
-- shop_order_items 同时引用 shop_orders 和 shop_products，先它。
DROP TABLE IF EXISTS shop_order_items;
DROP TABLE IF EXISTS shop_orders;
DROP TABLE IF EXISTS shop_order_seq;
DROP TABLE IF EXISTS shop_addresses;
DROP TABLE IF EXISTS shop_settings;

COMMIT;

-- 验证：五张表都没了（应返回 0 行）
--   SELECT tablename FROM pg_tables
--    WHERE schemaname = 'public'
--      AND tablename IN ('shop_addresses','shop_orders','shop_order_items',
--                        'shop_order_seq','shop_settings');
--
-- 008/009 的三张表仍在（应返回 3 行）
--   SELECT tablename FROM pg_tables
--    WHERE schemaname = 'public'
--      AND tablename IN ('shop_categories','shop_products','shop_cart_items');
