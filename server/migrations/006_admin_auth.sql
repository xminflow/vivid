-- 管理端登录鉴权 —— 新建三张表（发布新代码之前跑）
--
-- 背景：/api/admin 那组接口出的是全量客户姓名和手机号，此前完全没有鉴权，
-- 只能在内网用。这三张表给它补上登录。
--
-- 纯新增，不动任何已有表和列：建完表旧版本服务照常跑，不需要停机。
-- 但注意**发布新代码之后**后台立刻需要登录，运营要提前拿到超管账号。
--
-- 前置条件：schema.sql 里的 touch_updated_at() 已存在（users 表那一节建的）。
-- 幂等：CREATE ... IF NOT EXISTS + DROP TRIGGER IF EXISTS，可重复执行。
-- 回滚：跑 006_rollback.sql。

BEGIN;

-- 普通管理员。超级管理员**不在这张表里**——它由服务器配置文件
-- （ADMIN_SUPER_USERNAME / ADMIN_SUPER_PASSWORD）定义，见 app/admin_auth.py。
-- 所以这张表不需要 role 列：里面的每一行都是普通管理员。
CREATE TABLE IF NOT EXISTS admin_users (
  id            bigint      PRIMARY KEY,

  -- 登录用户名。限制成 ASCII 是为了不出现「看起来一样但不是同一个」的用户名
  -- （全角字母、前后空格、同形字符），那会让运营以为账号被盗
  username      text        NOT NULL UNIQUE
                            CONSTRAINT admin_users_username_format
                            CHECK (username ~ '^[a-zA-Z0-9_.-]{3,32}$'),

  -- 给人看的姓名，可留空
  display_name  text        NOT NULL DEFAULT ''
                            CHECK (length(display_name) <= 40),

  -- 格式见 app/security.py，自带算法与参数
  password_hash text        NOT NULL,

  status        text        NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'disabled')),

  last_login_at timestamptz,
  login_count   integer     NOT NULL DEFAULT 0,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS admin_users_touch_updated_at ON admin_users;
CREATE TRIGGER admin_users_touch_updated_at
  BEFORE UPDATE ON admin_users
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- 登录态。
--
-- admin_id 可空：超管不在 admin_users 里，没有可引用的行，它的会话
-- admin_id 是 NULL、is_super 是 true。CHECK 把两者锁成互斥，
-- 免得出现「既是超管又指向某个普通账号」这种解释不清的行。
--
-- ON DELETE CASCADE 是有意的：超管删掉一个账号，那人的会话跟着没，
-- 下一个请求就掉线。不用在删除接口里另写一句「顺手清会话」，也就不会漏。
CREATE TABLE IF NOT EXISTS admin_sessions (
  token       text        PRIMARY KEY,
  admin_id    bigint      REFERENCES admin_users (id) ON DELETE CASCADE,
  is_super    boolean     NOT NULL DEFAULT false,
  expires_at  timestamptz NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT admin_sessions_subject CHECK (
    (is_super AND admin_id IS NULL) OR (NOT is_super AND admin_id IS NOT NULL)
  )
);

-- 停用账号时要按 admin_id 批量删会话
CREATE INDEX IF NOT EXISTS admin_sessions_admin_idx ON admin_sessions (admin_id);

-- 登录失败计数，防爆破。
--
-- 为什么落库不放进程内存：云托管会多实例，内存计数等于没计数。
-- 超管密码是整个后台唯一的钥匙，这层保护不能省。
-- 按 username 记而不是按 IP：超管账号只有一个，锁它比锁 IP 有效，
-- 也不会因为运营在同一个办公室出口 IP 下而互相牵连。
CREATE TABLE IF NOT EXISTS admin_login_attempts (
  username     text        PRIMARY KEY,
  fail_count   integer     NOT NULL DEFAULT 0,
  locked_until timestamptz
);

COMMIT;

-- 验证：
--   三张表都在（应返回 3 行）
--     SELECT tablename FROM pg_tables
--      WHERE tablename IN ('admin_users', 'admin_sessions', 'admin_login_attempts');
--   用户名格式约束真的生效（应报 check constraint violation）
--     INSERT INTO admin_users (id, username, password_hash) VALUES (1, 'ab', 'x');
--   会话主体互斥约束真的生效（两条都应报错，随后回滚）
--     BEGIN;
--     INSERT INTO admin_sessions (token, admin_id, is_super, expires_at)
--       VALUES ('t1', NULL, false, now() + interval '1 hour');
--     ROLLBACK;
