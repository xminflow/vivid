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
                              'C端业主', '设计师', '地产开发商',
                              '酒店民宿业主', '家居行业经销商', '艺术家')),
  visit_date    date        NOT NULL,
  party_size    smallint    NOT NULL CHECK (party_size BETWEEN 1 AND 50),
  purpose       text        NOT NULL CHECK (purpose IN (
                              '展厅参观', '全案设计咨询', '装修与建材选购',
                              '家居产品选购', '商务合作', '其他')),
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
