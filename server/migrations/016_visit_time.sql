-- 预约到访时刻 —— 给 appointments 加一列 visit_time（发布新代码**之前**跑）
--
-- 背景：预约表原来只问到访日期，几点到只能靠回访电话再确认一次。表单加了
-- 「到访时间」的小时/分钟选择器，这一列就是它的落点。
--
-- 为什么可为空，不给默认值：
--   1. 存量记录只有日期。补一个假的时刻（比如统一 10:00）会让运营以为这些人
--      真的约了十点，比「没填」更糟——「没填」他知道要打电话问。
--   2. 服务端要先于小程序上线（见下面的顺序）。那几天里旧版本小程序提交的表单
--      不带 visitTime，NOT NULL 会让它们全部 500。
--
-- 发布顺序，不能反：
--   1. 跑这个脚本（老代码的 INSERT 不写这一列，多一个可空列照常跑）
--   2. 发布服务端 + 后台
--   3. 小程序发版
-- 提前发新服务端的后果：INSERT 写到不存在的列，预约提交全部 500。
--
-- 营业时段（周二至周日 10:00-19:00）不做 CHECK：那是运营随时会调的东西，
-- 写进约束里改一次营业时间就要跑一次迁移。可选范围由小程序的选择器卡住。
--
-- 幂等：ADD COLUMN IF NOT EXISTS，跑第二遍是空操作。
-- 回滚：跑 016_rollback.sql。

BEGIN;

ALTER TABLE appointments ADD COLUMN IF NOT EXISTS visit_time time;

COMMIT;

-- 验证：下面这条应返回 1
-- SELECT count(*) FROM information_schema.columns
--  WHERE table_schema = 'public'
--    AND table_name = 'appointments'
--    AND column_name = 'visit_time';
