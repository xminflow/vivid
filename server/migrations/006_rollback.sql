-- 006 的回滚：删掉三张表。
--
-- 删了之后后台就没有登录能力了，服务端代码必须先回退到不带鉴权的版本再跑这个，
-- 否则接口会因为查不到表而全部 500。
--
-- admin_sessions 有指向 admin_users 的外键，先删子表。

BEGIN;

DROP TABLE IF EXISTS admin_login_attempts;
DROP TABLE IF EXISTS admin_sessions;
DROP TABLE IF EXISTS admin_users;

COMMIT;
