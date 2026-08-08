-- 首页配图入库 —— 新建 home_media 表（发布新代码之前跑）
--
-- 背景：首页的首屏画廊、展厅实拍、活动海报原先写死在小程序 mock/home.js 里，
-- 换图要改代码 + 跑 scripts/upload_static.py + 发版，运营自己动不了。
-- 这张表让后台「首页图片」页直接维护。
--
-- 纯新增，不动任何已有表和列：
--   * 旧代码不引用这张表，建完表旧版本服务照常跑，不需要停机
--   * 表建完但表里没有数据时，GET /api/home 三个位都返回空数组，
--     小程序按约定回退到包内的默认图 —— 也就是「和现在完全一样」，
--     所以这一步和发布新代码之间的时间差不会有任何用户可见变化
--
-- 前置条件：schema.sql 里的 touch_updated_at() 已存在（users 表那一节建的）。
-- 幂等：CREATE ... IF NOT EXISTS + DROP TRIGGER IF EXISTS，可重复执行。
-- 回滚：跑 005_rollback.sql（直接删表，图还在 COS 上，重新配一遍即可）。

BEGIN;

CREATE TABLE IF NOT EXISTS home_media (
  id          bigint      PRIMARY KEY,
  slot        text        NOT NULL CHECK (slot IN ('hero', 'showroom', 'activity')),
  image_key   text        NOT NULL
                          CONSTRAINT home_media_image_key_len CHECK (length(image_key) <= 200)
                          CONSTRAINT home_media_image_key_prefix CHECK (image_key LIKE 'static/%'),
  sort_order  integer     NOT NULL DEFAULT 0,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS home_media_slot_idx ON home_media (slot, sort_order, id);

-- 活动位只放一张海报
CREATE UNIQUE INDEX IF NOT EXISTS home_media_activity_uniq
  ON home_media (slot) WHERE slot = 'activity';

DROP TRIGGER IF EXISTS home_media_touch_updated_at ON home_media;
CREATE TRIGGER home_media_touch_updated_at
  BEFORE UPDATE ON home_media
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

COMMIT;

-- 验证：
--   表在且列齐（应返回 6 行）
--     SELECT column_name, data_type, is_nullable FROM information_schema.columns
--      WHERE table_name = 'home_media' ORDER BY ordinal_position;
--   两个索引都在（应返回 home_media_slot_idx、home_media_activity_uniq）
--     SELECT indexname FROM pg_indexes WHERE tablename = 'home_media';
--   触发器在（应返回 1 行）
--     SELECT tgname FROM pg_trigger WHERE tgrelid = 'home_media'::regclass AND NOT tgisinternal;
--   活动位唯一约束真的生效（第二条 INSERT 应报 duplicate key，随后整个事务回滚）
--     BEGIN;
--     INSERT INTO home_media (id, slot, image_key) VALUES (1, 'activity', 'static/a.jpg');
--     INSERT INTO home_media (id, slot, image_key) VALUES (2, 'activity', 'static/b.jpg');
--     ROLLBACK;
