-- 小程序封面图 —— home_media 放行 slot = 'share'（发布新代码**之前**跑）
--
-- 背景：转发到聊天/群里的那张卡片配图，原先固定取首屏画廊的第一张
-- （antony-casa/utils/homeMedia.js 的 shareImage）。首图要满足首屏的构图，
-- 又要经得住微信 5:4 居中裁剪，一张图很难两头都好看。这一版让后台单独配一张，
-- **没配就还是用首图**，与现在的行为完全一致。
--
-- 改的是 slot 的 CHECK：从三个值放宽到四个。只放宽、不收紧：
--   * 旧代码只会写 hero / showroom / activity，放宽后照常跑，不需要停机
--   * 放宽完但还没人配 share 时，GET /api/home 的 share 是 null，
--     小程序退回首图 —— 也就是「和现在完全一样」，所以这一步和发布新代码
--     之间的时间差不会有任何用户可见变化
--
-- 发布顺序，不能反：
--   1. 跑这个脚本
--   2. 发布服务端 + 后台（后台这时才多出「小程序封面」那一组）
--   3. 小程序发版（不发也不影响：老版本不读 share 字段，继续用首图）
-- 提前发新服务端的后果：运营一保存封面图就撞 CHECK，接口 500。
--
-- 约束名 home_media_slot_check 是建表时 Postgres 给内联 CHECK 自动起的名字
-- （表名_列名_check）。005 建的库和 schema.sql 建的库都是这个名字。
--
-- 幂等：DROP CONSTRAINT IF EXISTS + 重建，可重复执行。
-- 回滚：跑 014_rollback.sql（要先把 share 那行删掉，脚本里带了）。

BEGIN;

ALTER TABLE home_media DROP CONSTRAINT IF EXISTS home_media_slot_check;
ALTER TABLE home_media ADD CONSTRAINT home_media_slot_check
  CHECK (slot IN ('hero', 'showroom', 'activity', 'share'));

-- 转发卡片只有一张配图。和活动位一样，唯一性写在库上，不只靠接口校验
CREATE UNIQUE INDEX IF NOT EXISTS home_media_share_uniq
  ON home_media (slot) WHERE slot = 'share';

COMMIT;

-- 验证：
--   约束放行了新值（应返回 1 行，随后整个事务回滚，不留数据）
--     BEGIN;
--     INSERT INTO home_media (id, slot, image_key) VALUES (1, 'share', 'static/a.jpg')
--       RETURNING slot;
--     ROLLBACK;
--   写错的 slot 仍然被挡（应报 violates check constraint）
--     BEGIN;
--     INSERT INTO home_media (id, slot, image_key) VALUES (2, 'banner', 'static/a.jpg');
--     ROLLBACK;
--   唯一索引在（应返回 home_media_share_uniq）
--     SELECT indexname FROM pg_indexes
--      WHERE tablename = 'home_media' AND indexname = 'home_media_share_uniq';
