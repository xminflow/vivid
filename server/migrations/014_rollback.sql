-- 014 的回滚：把 home_media.slot 收回三个值。
--
-- 什么时候用：新代码要退回旧版本。**退代码之后**再跑——顺序与 014 相反：
--   1. 小程序回退（不回退也行：老版本不读 share 字段，退回用首图）
--   2. 服务端 + 后台退回旧版本
--   3. 再跑这个脚本
-- 提前跑的后果：还在跑的新后台一保存封面图就撞 CHECK，接口 500。
--
-- ⚠️ 收紧约束之前必须先删掉 share 那一行，否则 ALTER 会因为存量数据不满足新约束
-- 而失败（整个事务回滚，库不会被改坏，但脚本跑不过去）。这里直接删——配置本身
-- 就一个对象键，图还在 COS 上，重新配一遍即可；要留档就先跑：
--   SELECT image_key FROM home_media WHERE slot = 'share';
--
-- 幂等：DELETE + DROP INDEX IF EXISTS + 重建约束，可重复执行。

BEGIN;

DELETE FROM home_media WHERE slot = 'share';

DROP INDEX IF EXISTS home_media_share_uniq;

ALTER TABLE home_media DROP CONSTRAINT IF EXISTS home_media_slot_check;
ALTER TABLE home_media ADD CONSTRAINT home_media_slot_check
  CHECK (slot IN ('hero', 'showroom', 'activity'));

COMMIT;

-- 验证：新值确实进不去了（应报 violates check constraint）
--   BEGIN;
--   INSERT INTO home_media (id, slot, image_key) VALUES (1, 'share', 'static/a.jpg');
--   ROLLBACK;
