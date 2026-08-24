-- 009 的回滚：删掉 shop_cart_items 表。
--
-- 什么时候用：购物车功能整体下线，或要退回不认识这张表的旧版本服务。
--
-- 丢的是「用户加了什么还没买」，属于**可再生数据**：用户重新加购即可，
-- 没有业务凭证价值，不需要像订单那样先备份。真要留底：
--   \copy (SELECT user_id, product_id, quantity, created_at FROM shop_cart_items)
--     TO 'shop_cart_items_backup.csv' CSV HEADER
--
-- 退回旧版本服务本身不需要跑这个：旧代码不引用这张表，留着也不影响。
--
-- 与 008_rollback.sql 的顺序：本表外键指向 shop_products，
-- 所以要先跑这个再跑 008_rollback，反过来会被外键挡住。
--
-- 幂等：DROP ... IF EXISTS，可重复执行。

BEGIN;

-- 表一删，它身上的索引、唯一约束和触发器跟着没了，不用单独 DROP
DROP TABLE IF EXISTS shop_cart_items;

COMMIT;

-- 验证：应返回 0 行
--   SELECT tablename FROM pg_tables WHERE tablename = 'shop_cart_items';
