-- 展厅小程序的表结构。
-- 建库：createdb antony_casa；本文件在该库里执行，可重复跑。

-- ============================================================
-- 用户表
-- ============================================================
-- 业务主键 id 是雪花 ID，由应用层 app/snowflake.py 生成，不用 bigserial：
--   1. 不暴露注册量（自增 id 谁都能数出来平台有多少用户）
--   2. 将来分库或和别的小程序合并数据时不会撞主键
-- 注意雪花 ID 超过 JS 的安全整数范围，出接口一律转成字符串，见 main.py。
--
-- openid 是微信在「本小程序」内对用户的唯一标识，wx.login 换来的，是这张表的
-- 自然键。需求文档 2.1 定了各小程序独立、不共享用户数据，所以这里一库一个小程序，
-- openid 单列唯一即可；将来若几个小程序合到一个库，把唯一约束换成 (app_id, openid)。
CREATE TABLE IF NOT EXISTS users (
  id            bigint      PRIMARY KEY,

  -- 微信身份
  openid        text        NOT NULL UNIQUE,
  -- 同一开放平台下跨应用的标识。本期用不上（各小程序独立），先存着，
  -- 二期若要打通矩阵账号，没有历史 unionid 就补不回来了
  unionid       text,
  -- 解密手机号等加密数据要用，每次登录都会变。属于密钥，绝不出接口
  session_key   text        NOT NULL DEFAULT '',

  -- 自定义登录态。小程序拿 token 调后续接口，替代每次都传 openid
  token             text UNIQUE,
  token_expires_at  timestamptz,

  -- 微信授权资料（用户点头像昵称填写时才有，可能一直是空）
  nickname      text        NOT NULL DEFAULT '' CHECK (length(nickname) <= 60),
  avatar_url    text        NOT NULL DEFAULT '',
  -- 用户在「我的」页用 open-type="chooseAvatar" 选的头像，存 COS 对象键不存 URL：
  -- 桶是私有的，地址要服务端现签（见 app/cos.py）；且桶和地域将来会变，存 URL 会全部失效。
  -- 与 avatar_url 分开两列：那列是微信给的外链，来源和生命周期都不同，混用会互相覆盖
  avatar_key    text        NOT NULL DEFAULT ''
                            CONSTRAINT users_avatar_key_len CHECK (length(avatar_key) <= 200),

  -- 「我的信息」表单，用户自己填，都可以留空
  member_name   text        NOT NULL DEFAULT '' CHECK (length(member_name) <= 40),
  phone         text        NOT NULL DEFAULT ''
                            CHECK (phone = '' OR phone ~ '^1[3-9][0-9]{9}$'),
  email         text        NOT NULL DEFAULT ''
                            CHECK (email = '' OR email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
  -- 上限不写 CURRENT_DATE：CHECK 里不允许非 immutable 函数，生日不能是未来由模型层挡
  birthday      date        CHECK (birthday IS NULL OR birthday >= DATE '1930-01-01'),
  -- 与小程序 mock/mine.js 的 genders 逐字一致，'' 表示没填
  gender        text        NOT NULL DEFAULT ''
                            CHECK (gender IN ('', '女', '男', '不便告知')),
  -- region picker 给的是 [省, 市, 区]，拆成三列存，后台好按地区筛
  province      text        NOT NULL DEFAULT '',
  city          text        NOT NULL DEFAULT '',
  district      text        NOT NULL DEFAULT '',

  -- 会员。一期不分等级、不收费，只有是/否两种状态（需求文档 2.2）
  is_member         boolean NOT NULL DEFAULT false,
  member_since      timestamptz,
  -- 有效期需求方还没定，先留字段；NULL 表示不过期
  member_expires_at timestamptz,

  -- 运营。后台要能封号（需求文档 4.6 用户管理）
  status        text        NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'banned')),
  last_login_at timestamptz,
  login_count   integer     NOT NULL DEFAULT 0,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- 上面的 CREATE TABLE 对已经建好的库不生效，avatar_key 得单独补。
-- 带默认值加列在 PG 11+ 不重写全表，存量行直接读到 ''，旧版本代码不引用该列，可直接回滚
ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_key text NOT NULL DEFAULT '';

-- 约束得单独补，且 Postgres 没有 ADD CONSTRAINT IF NOT EXISTS。
-- 已经加过就吞掉 duplicate_object，让本文件保持可重复执行
DO $$
BEGIN
  ALTER TABLE users
    ADD CONSTRAINT users_avatar_key_len CHECK (length(avatar_key) <= 200);
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

-- 后台按注册时间倒序翻列表
CREATE INDEX IF NOT EXISTS users_created_at_idx ON users (created_at DESC);
-- 客服按手机号找人。没填手机号的用户不进索引
CREATE INDEX IF NOT EXISTS users_phone_idx ON users (phone) WHERE phone <> '';
CREATE INDEX IF NOT EXISTS users_unionid_idx ON users (unionid) WHERE unionid IS NOT NULL;

-- updated_at 交给触发器，免得每条 UPDATE 都要记得带上
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS users_touch_updated_at ON users;
CREATE TRIGGER users_touch_updated_at
  BEFORE UPDATE ON users
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- ============================================================
-- 预约参观登记表
-- ============================================================
CREATE TABLE IF NOT EXISTS appointments (
  id            bigserial PRIMARY KEY,

  -- 表单字段
  name          text        NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 40),
  phone         text        NOT NULL CHECK (phone ~ '^1[3-9][0-9]{9}$'),
  visitor_type  text        NOT NULL CHECK (visitor_type IN (
                              '业主', '设计师', '地产圈',
                              '家居圈', '酒店民宿圈', '艺术圈')),
  visit_date    date        NOT NULL,
  party_size    smallint    NOT NULL CHECK (party_size BETWEEN 1 AND 50),
  purpose       text        NOT NULL CHECK (purpose IN (
                              '展厅参观', '全案设计咨询', '装修建材订购',
                              '家具软装选购', '商务合作', '其他')),
  note          text        NOT NULL DEFAULT '' CHECK (length(note) <= 500),

  -- 从哪张展厅卡片点进来的，可为空（用户也可能从别处进表单）
  space_id      text,

  -- 谁提交的。未登录也能填表，所以可为空
  user_id       bigint      REFERENCES users (id),

  -- 运营用
  status        text        NOT NULL DEFAULT 'new'
                            CHECK (status IN ('new', 'confirmed', 'visited', 'cancelled')),
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- 上面的 CREATE TABLE 对已经建好的库不生效，user_id 得单独补
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS user_id bigint REFERENCES users (id);

-- 后台按提交时间倒序翻列表
CREATE INDEX IF NOT EXISTS appointments_created_at_idx
  ON appointments (created_at DESC);

-- 「我的」页要按人拉自己的预约记录
CREATE INDEX IF NOT EXISTS appointments_user_id_idx
  ON appointments (user_id, created_at DESC) WHERE user_id IS NOT NULL;

-- 同一手机号同一天只登记一次，防重复提交
CREATE UNIQUE INDEX IF NOT EXISTS appointments_phone_date_uniq
  ON appointments (phone, visit_date);

-- ---------------------------------------------------------------------------
-- 服务申请。五个服务共用一张表：
-- 各服务的表单字段差异很大（设计类问面积预算，商品类问品牌成色），
-- 拆五张表会让后台查列表要 union 五次，而这些字段运营只读不查，
-- 所以除了公共的联系方式和状态，其余字段整体存 jsonb。
CREATE TABLE IF NOT EXISTS service_applications (
  id            bigserial PRIMARY KEY,

  service_id    text        NOT NULL CHECK (service_id IN (
                              'design', 'hardfit', 'buyer', 'aftersale', 'resale')),

  -- 公共字段。没有联系方式的申请运营跟不了，必填
  name          text        NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 40),
  phone         text        NOT NULL CHECK (phone ~ '^1[3-9][0-9]{9}$'),

  -- 该服务自己的表单字段，键是字段 id
  fields        jsonb       NOT NULL DEFAULT '{}'::jsonb,

  -- 上传的图片，键是上传组 id，值是 COS 对象键数组
  -- 只存键不存 URL：桶和地域会变，URL 由服务端按需签发
  images        jsonb       NOT NULL DEFAULT '{}'::jsonb,

  status        text        NOT NULL DEFAULT 'new'
                            CHECK (status IN ('new', 'contacted', 'closed')),
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS service_applications_created_at_idx
  ON service_applications (created_at DESC);

CREATE INDEX IF NOT EXISTS service_applications_service_idx
  ON service_applications (service_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- 首页配图。原先写死在小程序的 mock/home.js 里，换图要改代码 + 跑上传脚本，
-- 运营自己动不了。挪进库后由后台「首页图片」页维护，小程序拉接口拿。
--
-- 三个位置（slot）共用一张表：字段完全一样（一个对象键 + 一个顺序），
-- 拆三张表只是把同一份读写逻辑抄三遍。
--
-- 业务主键用雪花 ID（app/snowflake.py 生成），与 users 表一致：
-- 这批记录会随后台操作反复增删，自增 id 的空洞没有意义，将来多小程序合库也不撞。
CREATE TABLE IF NOT EXISTS home_media (
  id          bigint      PRIMARY KEY,

  -- 首页上的位置。值与 app/models.py 的 HomeSlot、小程序 mock/home.js 的字段对应
  --   hero     首屏画廊
  --   showroom 展厅预约那一组实拍
  --   activity 近期活动海报
  slot        text        NOT NULL CHECK (slot IN ('hero', 'showroom', 'activity')),

  -- COS 对象键，不存 URL：桶和地域将来会变，存 URL 会全部失效。
  -- 这里的键一律在 static/ 前缀下——首页图要能被任何人直接加载，
  -- 与 uploads/ 那些需要现签地址的用户上传图不是一回事（见 app/cos.py）
  image_key   text        NOT NULL
                          CONSTRAINT home_media_image_key_len CHECK (length(image_key) <= 200)
                          CONSTRAINT home_media_image_key_prefix CHECK (image_key LIKE 'static/%'),

  -- 同一个 slot 内的展示顺序，从 0 开始。后台整组保存时按数组下标重写
  sort_order  integer     NOT NULL DEFAULT 0,

  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- 小程序每次进首页都按 slot 取一遍，排序键跟着一起进索引，避免额外排序
CREATE INDEX IF NOT EXISTS home_media_slot_idx ON home_media (slot, sort_order, id);

-- 活动位只放一张海报（首页是整张铺开的，不是轮播）。约束写在库上而不是只靠
-- 接口校验：接口将来加别的入口时不会漏掉这条
CREATE UNIQUE INDEX IF NOT EXISTS home_media_activity_uniq
  ON home_media (slot) WHERE slot = 'activity';

DROP TRIGGER IF EXISTS home_media_touch_updated_at ON home_media;
CREATE TRIGGER home_media_touch_updated_at
  BEFORE UPDATE ON home_media
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
