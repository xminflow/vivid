-- 微信手机号快速验证的每日配额 —— 新建一张计数表（发布新代码**之前**跑）
--
-- 背景：新接口 POST /api/users/me/phone 用 getPhoneNumber 的 code 换手机号明文，
-- 微信对这个接口**按调用次数收费**。按钮在四个页面上，用户可以无限点，抓包重放
-- 更是没有上限——没有服务端配额的话，调用量直接等于账单。
--
-- 纯新增，不动任何已有表和列：建完表旧版本服务照常跑，不需要停机。
-- 新代码发布之前跑：反过来（先发代码）会让换手机号的接口在建表前每次都 500。
--
-- 为什么这张表没有 snowflake 业务主键：它不是实体表，是一行一个用户的计数器，
-- 与 006 建的 admin_login_attempts 同一类。user_id 本身就是天然主键，
-- 额外发一个 id 只会让「一个用户一行」这条不变式变得需要靠索引去维持。
--
-- 幂等：CREATE TABLE IF NOT EXISTS，可重复执行。
-- 回滚：跑 015_rollback.sql。

BEGIN;

CREATE TABLE IF NOT EXISTS user_phone_quota (
  -- 用户注销时计数跟着删。ON DELETE CASCADE 而不是留孤儿行：
  -- 这张表没有审计价值，留着只会让 user_id 指向不存在的人
  user_id    bigint      PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,

  -- 计数所属的自然日（东八区）。跨天不清表、只在写入时发现日期变了就归零，
  -- 省掉一个定时任务，也就不存在「定时任务没跑起来导致所有人被锁一天」
  quota_date date        NOT NULL,

  used       integer     NOT NULL DEFAULT 0 CHECK (used >= 0),
  updated_at timestamptz NOT NULL DEFAULT now()
);

COMMIT;

-- 验证：
--   表在（应返回 user_phone_quota）
--     SELECT tablename FROM pg_tables WHERE tablename = 'user_phone_quota';
--   外键级联真的挂上了（应返回 user_phone_quota_user_id_fkey / c）
--     SELECT conname, confdeltype FROM pg_constraint
--      WHERE conrelid = 'user_phone_quota'::regclass AND contype = 'f';
--   同一个用户重复计数只会更新那一行（应返回 1 行、used = 2，随后整个事务回滚）
--     BEGIN;
--     INSERT INTO user_phone_quota (user_id, quota_date, used)
--       SELECT id, current_date, 1 FROM users LIMIT 1
--       ON CONFLICT (user_id) DO UPDATE SET used = user_phone_quota.used + 1;
--     INSERT INTO user_phone_quota (user_id, quota_date, used)
--       SELECT id, current_date, 1 FROM users LIMIT 1
--       ON CONFLICT (user_id) DO UPDATE SET used = user_phone_quota.used + 1;
--     SELECT used FROM user_phone_quota;
--     ROLLBACK;
