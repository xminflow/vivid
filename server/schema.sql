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

-- 微信手机号快速验证的每日配额，一个用户一行。
--
-- 那个接口按调用次数收费，而按钮在四个页面上、用户可以无限点，所以配额是这条链路
-- 唯一真正护着账单的一层——客户端的按钮置灰对抓包重放毫无作用。
-- 与 admin_login_attempts 同类：计数器，不是实体表，所以没有 snowflake 主键。
-- 跨天不清表，写入时发现 quota_date 变了就归零，省掉一个定时任务。
CREATE TABLE IF NOT EXISTS user_phone_quota (
  user_id    bigint      PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
  quota_date date        NOT NULL,
  used       integer     NOT NULL DEFAULT 0 CHECK (used >= 0),
  updated_at timestamptz NOT NULL DEFAULT now()
);

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
  -- 几点到。可为空：这一列是后加的（migrations/016），存量记录只有日期；
  -- 不做营业时段的 CHECK——营业时间是运营会改的东西，写死在约束里改一次要跑一次
  -- 迁移，可选范围由小程序的选择器卡（booking.js 的 OPEN_TIME / CLOSE_TIME）
  visit_time    time,
  party_size    smallint    NOT NULL CHECK (party_size BETWEEN 1 AND 50),
  purpose       text        NOT NULL CHECK (purpose IN (
                              '展厅参观', '全案设计咨询', '装修建材订购',
                              '家具软装选购', '商务合作', '其他')),
  note          text        NOT NULL DEFAULT '' CHECK (length(note) <= 500),

  -- 从哪张展厅卡片点进来的，可为空（用户也可能从别处进表单）
  space_id      text,

  -- 谁提交的。未登录也能填表，所以可为空
  user_id       bigint      REFERENCES users (id),

  created_at    timestamptz NOT NULL DEFAULT now()
);

-- 这张表曾有一个 status（跟进状态）列。它只有默认值没有写入口，后台改用
-- 「删除记录」清理线索，已随 migrations/007_drop_status.sql 删除。
-- 已经建好的库不会因为这里少了一行就把列删掉，要跑那个迁移脚本。

-- 上面的 CREATE TABLE 对已经建好的库不生效，后加的两列得单独补
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS user_id bigint REFERENCES users (id);
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS visit_time time;

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
-- 所以除了公共的联系方式，其余字段整体存 jsonb。
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

  created_at    timestamptz NOT NULL DEFAULT now()
);

-- 同 appointments：原来的 status 列已随 migrations/007_drop_status.sql 删除

CREATE INDEX IF NOT EXISTS service_applications_created_at_idx
  ON service_applications (created_at DESC);

CREATE INDEX IF NOT EXISTS service_applications_service_idx
  ON service_applications (service_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- 首页配图。原先写死在小程序的 mock/home.js 里，换图要改代码 + 跑上传脚本，
-- 运营自己动不了。挪进库后由后台「首页图片」页维护，小程序拉接口拿。
--
-- 几个位置（slot）共用一张表：字段完全一样（一个对象键 + 一个顺序），
-- 一个位置拆一张表只是把同一份读写逻辑抄几遍。转发卡片的封面图（share）不在
-- 首页上，但配置方式与首页图一模一样，也放在这张表里，不另起一套。
--
-- 业务主键用雪花 ID（app/snowflake.py 生成），与 users 表一致：
-- 这批记录会随后台操作反复增删，自增 id 的空洞没有意义，将来多小程序合库也不撞。
CREATE TABLE IF NOT EXISTS home_media (
  id          bigint      PRIMARY KEY,

  -- 位置。值与 app/models.py 的 HomeSlot、小程序 mock/home.js 的字段对应
  --   hero     首屏画廊
  --   showroom 展厅预约那一组实拍
  --   activity 近期活动海报
  --   share    转发卡片的封面图（小程序封面），不在首页上，没配时用 hero 首图
  slot        text        NOT NULL CHECK (slot IN ('hero', 'showroom', 'activity', 'share')),

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

-- 转发卡片也只有一张配图，同理
CREATE UNIQUE INDEX IF NOT EXISTS home_media_share_uniq
  ON home_media (slot) WHERE slot = 'share';

DROP TRIGGER IF EXISTS home_media_touch_updated_at ON home_media;
CREATE TRIGGER home_media_touch_updated_at
  BEFORE UPDATE ON home_media
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- ============================================================
-- 管理端账号
-- ============================================================
-- 后台 /api/admin 那组接口出的是全量客户资料，必须登录才能看。
--
-- 超级管理员**不在 admin_users 里**：它由服务器配置文件
-- （ADMIN_SUPER_USERNAME / ADMIN_SUPER_PASSWORD）定义，见 app/admin_auth.py。
-- 所以这张表不需要 role 列——里面每一行都是普通管理员，由超管在后台创建。
CREATE TABLE IF NOT EXISTS admin_users (
  id            bigint      PRIMARY KEY,
  username      text        NOT NULL UNIQUE
                            CONSTRAINT admin_users_username_format
                            CHECK (username ~ '^[a-zA-Z0-9_.-]{3,32}$'),
  display_name  text        NOT NULL DEFAULT ''
                            CHECK (length(display_name) <= 40),
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

-- 登录态。admin_id 可空是因为超管不入库，它的会话 admin_id 是 NULL、
-- is_super 是 true，CHECK 把两者锁成互斥。
-- ON DELETE CASCADE：删号即踢下线，不用另写清会话的代码。
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

CREATE INDEX IF NOT EXISTS admin_sessions_admin_idx ON admin_sessions (admin_id);

-- 登录失败计数，防爆破。落库不放内存：云托管多实例时内存计数等于没计数
CREATE TABLE IF NOT EXISTS admin_login_attempts (
  username     text        PRIMARY KEY,
  fail_count   integer     NOT NULL DEFAULT 0,
  locked_until timestamptz
);

-- ============================================================
-- 安玺·集 商品目录
-- ============================================================
-- antony-casa 小程序「安玺·集」购物板块的商品与分类。
-- 交易链路（购物车、地址、订单、订单行）属于阶段二，届时另行加表。
-- 设计见 docs/superpowers/specs/2026-08-10-anxi-ji-design.md。

-- 商品分类。单层，商品必属其一。
--
-- 只能停用、不能删除：停用会把该分类下所有在售商品一并改写为下架，
-- 且重新启用**不会**自动恢复（需要后台点「批量上架」）。
-- 这样做是为了让 shop_products.status 保持「商品能否被购买」的唯一判据——
-- 若分类状态能隐式改变可购性，后台会显示「在售」而用户买不到。
CREATE TABLE IF NOT EXISTS shop_categories (
  id          bigint      PRIMARY KEY,
  name        text        NOT NULL UNIQUE
                          CONSTRAINT shop_categories_name_len CHECK (length(name) BETWEEN 1 AND 20),
  sort_order  integer     NOT NULL DEFAULT 0,
  status      text        NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active', 'disabled')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS shop_categories_listing_idx
  ON shop_categories (status, sort_order DESC, id);

DROP TRIGGER IF EXISTS shop_categories_touch_updated_at ON shop_categories;
CREATE TRIGGER shop_categories_touch_updated_at
  BEFORE UPDATE ON shop_categories
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- 商品。无 SKU、无库存：一个商品一个价格，status 是能否被购买的唯一判据。
--
-- category_id 不写 ON DELETE：默认的 NO ACTION 正好让「分类下还有商品就删不掉」
-- 由数据库兜底。接口层本来就不提供删除分类的入口，这是第二道防线。
--
-- 三个 jsonb 列都是**有序数组**，顺序即展示顺序：
--   images        COS key 数组，[0] 兼作列表页封面
--   detail_images COS key 数组，详情正文按序渲染
--   params        [{"name": "材质", "value": "实木"}]，展示型参数，不影响价格与可购性
-- 数量上限写进 CHECK，避免应用层漏校验就把几百张图塞进来。
-- 单条 key 的前缀与长度校验放在 models.py（check_static_key），SQL 里不重复。
CREATE TABLE IF NOT EXISTS shop_products (
  id             bigint      PRIMARY KEY,
  category_id    bigint      NOT NULL REFERENCES shop_categories (id),
  title          text        NOT NULL
                             CONSTRAINT shop_products_title_len CHECK (length(title) BETWEEN 1 AND 60),
  summary        text        NOT NULL DEFAULT ''
                             CONSTRAINT shop_products_summary_len CHECK (length(summary) <= 500),
  price_cents    integer     NOT NULL
                             CONSTRAINT shop_products_price_positive CHECK (price_cents > 0),
  images         jsonb       NOT NULL DEFAULT '[]'::jsonb
                             CONSTRAINT shop_products_images_shape
                             CHECK (jsonb_typeof(images) = 'array' AND jsonb_array_length(images) <= 10),
  detail_images  jsonb       NOT NULL DEFAULT '[]'::jsonb
                             CONSTRAINT shop_products_detail_images_shape
                             CHECK (jsonb_typeof(detail_images) = 'array' AND jsonb_array_length(detail_images) <= 20),
  params         jsonb       NOT NULL DEFAULT '[]'::jsonb
                             CONSTRAINT shop_products_params_shape
                             CHECK (jsonb_typeof(params) = 'array' AND jsonb_array_length(params) <= 20),
  status         text        NOT NULL DEFAULT 'off'
                             CHECK (status IN ('active', 'off')),
  sort_order     integer     NOT NULL DEFAULT 0,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS shop_products_listing_idx
  ON shop_products (status, sort_order DESC, created_at DESC, id);

CREATE INDEX IF NOT EXISTS shop_products_category_idx
  ON shop_products (category_id);

DROP TRIGGER IF EXISTS shop_products_touch_updated_at ON shop_products;
CREATE TRIGGER shop_products_touch_updated_at
  BEFORE UPDATE ON shop_products
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- 购物车行。只存「谁、要哪件、要几个」，**不存价格**。
--
-- 不存价格快照是刻意的：购物车每次打开都实时回查商品的当前价格与在售状态，
-- 价格以结算那一刻为准。存快照会带来「加购时是 9999、现在是 12999，按哪个结算」
-- 这种没有好答案的问题。需要固化价格的是**订单**，那里必须存快照，两件事职责分开。
--
-- 两个外键都 CASCADE：删用户连购物车一起清；商品被硬删时购物车里那一行跟着消失。
-- 这和订单行将来的 ON DELETE RESTRICT 正好相反——订单是凭证不能动，购物车只是暂存。
CREATE TABLE IF NOT EXISTS shop_cart_items (
  id          bigint      PRIMARY KEY,
  user_id     bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  product_id  bigint      NOT NULL REFERENCES shop_products (id) ON DELETE CASCADE,
  quantity    integer     NOT NULL DEFAULT 1
                          CONSTRAINT shop_cart_items_quantity_range
                          CHECK (quantity BETWEEN 1 AND 99),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  -- 同一件商品在一个人的车里只有一行，重复加购是累加数量。靠唯一约束而不是
  -- 「先查再插」：两次加购几乎同时到达时，查完到插入之间会各自认为「还没有」
  CONSTRAINT shop_cart_items_one_row_per_product UNIQUE (user_id, product_id)
);

CREATE INDEX IF NOT EXISTS shop_cart_items_user_idx
  ON shop_cart_items (user_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS shop_cart_items_product_idx
  ON shop_cart_items (product_id);

DROP TRIGGER IF EXISTS shop_cart_items_touch_updated_at ON shop_cart_items;
CREATE TRIGGER shop_cart_items_touch_updated_at
  BEFORE UPDATE ON shop_cart_items
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- 收货地址簿。是「用户当前的地址」，不是「订单用过的地址」——
-- 订单存的是下单那一刻的快照（见 shop_orders 的六个地址列），
-- 所以这张表可以随用户任意改删，历史订单不受影响。
CREATE TABLE IF NOT EXISTS shop_addresses (
  id          bigint      PRIMARY KEY,
  user_id     bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  receiver    text        NOT NULL
                          CONSTRAINT shop_addresses_receiver_len CHECK (length(receiver) BETWEEN 1 AND 20),
  -- 与 users.phone / appointments.phone 同一条正则
  phone       text        NOT NULL CHECK (phone ~ '^1[3-9][0-9]{9}$'),
  province    text        NOT NULL
                          CONSTRAINT shop_addresses_province_len CHECK (length(province) BETWEEN 1 AND 100),
  city        text        NOT NULL
                          CONSTRAINT shop_addresses_city_len CHECK (length(city) BETWEEN 1 AND 100),
  district    text        NOT NULL
                          CONSTRAINT shop_addresses_district_len CHECK (length(district) BETWEEN 1 AND 100),
  detail      text        NOT NULL
                          CONSTRAINT shop_addresses_detail_len CHECK (length(detail) BETWEEN 1 AND 100),
  is_default  boolean     NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS shop_addresses_user_idx
  ON shop_addresses (user_id, created_at DESC, id DESC);

-- 每人至多一个默认地址。部分唯一索引而不是应用层「先清旧的再设新的」：
-- 那两步之间并发进来第二个请求就会留下两个默认地址，界面上只显示一个，
-- 表现为「设了没生效」
CREATE UNIQUE INDEX IF NOT EXISTS shop_addresses_one_default_per_user
  ON shop_addresses (user_id) WHERE is_default;

DROP TRIGGER IF EXISTS shop_addresses_touch_updated_at ON shop_addresses;
CREATE TRIGGER shop_addresses_touch_updated_at
  BEFORE UPDATE ON shop_addresses
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- 单号序号。单号格式 AX + YYYYMMDD + 6 位序号，如 AX20260810000137。
-- 取号是单条原子语句（INSERT ... ON CONFLICT DO UPDATE ... RETURNING），
-- 冲突分支的 UPDATE 持行锁到事务结束，并发下自然串行，不需要应用层加锁。
--
-- 不用 sequence：它不回滚（下单失败会咬掉号，单号出现空洞），也不好按天归零。
-- 该单号同时作为微信支付的 out_trade_no（微信要求 6–32 位、商户内唯一，16 位正好）。
CREATE TABLE IF NOT EXISTS shop_order_seq (
  day   date    PRIMARY KEY,
  next  integer NOT NULL
                CONSTRAINT shop_order_seq_next_range CHECK (next BETWEEN 1 AND 999999)
);

-- 订单。
--
-- 状态机（旁支不可逆）：
--   pending_pay ──支付成功──> pending_ship ──发货──> pending_receive ──收货──> completed
--        │                                                              ↑
--        ├──超时/用户取消──> closed                          （10 天自动确认）
--        └──（已支付后由超管退款）──────> refunded
--
-- 六个地址列是**快照**，不存 address_id 引用：用户改了地址簿或删了那条地址，
-- 都不能改变已经下过的单。与订单行存 title/price 快照同理。
--
-- 只存 total_cents 一个总额，不留运费/优惠分项——本期没有这些概念，
-- 提前留列只会让人以为它们有意义。
CREATE TABLE IF NOT EXISTS shop_orders (
  id                bigint      PRIMARY KEY,
  -- 前两位是**环境前缀**（ORDER_NO_PREFIX，生产 AX、开发 AD），后面是日期 + 当日序号。
  -- 为什么要按环境分：微信支付没有沙箱，两个环境共用同一个商户号，而序号取自
  -- 各自库里的 shop_order_seq，不分前缀就必然发出同名的 out_trade_no，
  -- 于是超时关单会去关对方的单、每日对账会把对方的退款同步到自己身上。
  -- 见 app/order_no.py 与 migrations/012_payment_hardening.sql
  order_no          text        NOT NULL UNIQUE
                                CONSTRAINT shop_orders_no_format
                                CHECK (order_no ~ '^[A-Z]{2}[0-9]{14}$'),
  user_id           bigint      NOT NULL REFERENCES users (id),
  status            text        NOT NULL DEFAULT 'pending_pay'
                                CHECK (status IN ('pending_pay', 'pending_ship', 'pending_receive',
                                                  'completed', 'closed', 'refunded')),
  -- 从购物车结算还是详情页「立即购买」。唯一用途是**支付成功时决定要不要清车**：
  -- 不存的话到回调那一刻就无从判断，只能「在车里就顺手清掉」，
  -- 那会让详情页直接购买莫名其妙地清掉用户车里的同款
  source            text        NOT NULL DEFAULT 'cart'
                                CHECK (source IN ('cart', 'direct')),
  total_cents       integer     NOT NULL
                                CONSTRAINT shop_orders_total_positive CHECK (total_cents > 0),

  receiver          text        NOT NULL,
  phone             text        NOT NULL,
  province          text        NOT NULL,
  city              text        NOT NULL,
  district          text        NOT NULL,
  detail            text        NOT NULL,

  transaction_id    text,
  paid_at           timestamptz,
  -- 主动查单与超时扫描共用的节流字段：上次向微信查这笔单的时刻
  last_query_at     timestamptz,
  -- 上次向微信**下单**的时刻。与 last_query_at 是两个接口、两套频率限制，
  -- 共用一个字段的话，用户下拉刷新（查单）会把「去支付」（下单）的节流也顶掉
  last_pay_at       timestamptz,
  -- 超时关单扫描连续处理失败的次数。达到上限后不再选中——
  -- 没有这个计数的话，永远处理不掉的单会一直占着 `ORDER BY created_at LIMIT 50`
  -- 的队头，攒够一屏就让整个扫描停摆。见 app/shop_pay.py 的 SWEEP_MAX_ATTEMPTS
  sweep_attempts    integer     NOT NULL DEFAULT 0,

  shipping_type     text        CHECK (shipping_type IN ('express', 'local', 'none')),
  shipping_company  text,
  tracking_no       text,
  shipped_at        timestamptz,
  -- 物流信息**回传给微信**的时刻。与 shipped_at 是两件事：那是运营在我们后台
  -- 点发货的时刻，这是我们成功告诉微信的时刻。回传是一次会失败的网络调用，
  -- 分开记才能表达「已发货但没传成功」这个必须被重试的状态。
  -- 微信对实物交易有时限要求，超时不传会判发货延迟并影响交易权限
  shipping_uploaded_at timestamptz,

  received_at       timestamptz,

  closed_at         timestamptz,
  -- never_submitted：微信侧根本没有这笔单（下单那一刻就没提交成功）。
  -- 与 timeout 分开——「用户没在时限内付」和「压根没送到微信」的排查方向完全不同
  close_reason      text        CONSTRAINT shop_orders_close_reason_check
                                CHECK (close_reason IN ('timeout', 'user_cancel',
                                                        'never_submitted')),

  refunded_at       timestamptz,
  refund_reason     text,
  refund_id         text,

  remark            text        NOT NULL DEFAULT '',

  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),

  -- 走快递就必须有运单号，另两档必须没有。没有这条约束的话，
  -- 「选了快递但运单号为空」会一路流到微信的发货信息录入接口才报错
  CONSTRAINT shop_orders_express_needs_tracking CHECK (
    shipping_type IS DISTINCT FROM 'express' OR (tracking_no IS NOT NULL AND tracking_no <> '')
  )
);

CREATE INDEX IF NOT EXISTS shop_orders_user_idx
  ON shop_orders (user_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS shop_orders_status_idx
  ON shop_orders (status, created_at DESC, id DESC);

-- 「已发货但还没把物流信息传给微信」的单，给重试和人工排查用。
-- 部分索引：绝大多数订单要么没发货、要么已经传成功，落进来的只有出问题的那几笔
CREATE INDEX IF NOT EXISTS shop_orders_pending_upload_idx
  ON shop_orders (shipped_at)
  WHERE shipped_at IS NOT NULL AND shipping_uploaded_at IS NULL;

-- 回调幂等在数据库层的第二道防线：应用层已经用 SELECT ... FOR UPDATE 锁行判重，
-- 但回调会被微信重投、也可能与主动查单同时到达，多进程下只有唯一索引挡得住
CREATE UNIQUE INDEX IF NOT EXISTS shop_orders_transaction_uniq
  ON shop_orders (transaction_id) WHERE transaction_id IS NOT NULL;

DROP TRIGGER IF EXISTS shop_orders_touch_updated_at ON shop_orders;
CREATE TRIGGER shop_orders_touch_updated_at
  BEFORE UPDATE ON shop_orders
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- 订单行。标题、单价、封面全是**下单那一刻的快照**，
-- 任何场景（列表、详情、退款、对账）都不回查 shop_products 的当前值。
--
-- 两个外键方向相反：
--   order_id    CASCADE  —— 订单没了行也没有意义（但订单本身不提供删除）
--   product_id  RESTRICT —— **卖过的商品永远删不掉，只能下架**。由数据库强制，
--                           后台的删除接口把外键违约翻译成 409 并列出引用它的订单号
CREATE TABLE IF NOT EXISTS shop_order_items (
  id                    bigint      PRIMARY KEY,
  order_id              bigint      NOT NULL REFERENCES shop_orders (id) ON DELETE CASCADE,
  product_id            bigint      NOT NULL REFERENCES shop_products (id) ON DELETE RESTRICT,
  title_snapshot        text        NOT NULL,
  price_cents_snapshot  integer     NOT NULL
                                    CONSTRAINT shop_order_items_price_positive
                                    CHECK (price_cents_snapshot > 0),
  image_snapshot        text        NOT NULL DEFAULT '',
  quantity              integer     NOT NULL
                                    CONSTRAINT shop_order_items_quantity_range
                                    CHECK (quantity BETWEEN 1 AND 99),
  created_at            timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS shop_order_items_order_idx
  ON shop_order_items (order_id, id);

CREATE INDEX IF NOT EXISTS shop_order_items_product_idx
  ON shop_order_items (product_id);

-- 板块设置。单行表，id 恒为 1。加列比加行更容易看出有哪些设置，
-- 也免去每次读都要判空
CREATE TABLE IF NOT EXISTS shop_settings (
  id              smallint    PRIMARY KEY CHECK (id = 1),
  -- 客服二维码的 COS key。空串表示还没配，小程序据此隐藏「联系客服」入口
  service_qr_key  text        NOT NULL DEFAULT '',
  updated_at      timestamptz NOT NULL DEFAULT now()
);

INSERT INTO shop_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

DROP TRIGGER IF EXISTS shop_settings_touch_updated_at ON shop_settings;
CREATE TRIGGER shop_settings_touch_updated_at
  BEFORE UPDATE ON shop_settings
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- 支付异常台账。
--
-- 收到「支付成功」却对不上订单时写这里：金额与订单不符、单号在库里不存在、
-- 微信没给支付单号。这三种都意味着**钱已经收了但订单没有正常流转**。
--
-- 为什么必须落库而不是只记日志：这几条路径最终都要向微信返回 SUCCESS
-- （让它别再重投——重投一百次结果也一样），于是唯一的线索就只剩一行 error 日志，
-- 而没有人会主动去翻日志。落库之后它是后台里一个能看见、能标记已处理的列表。
--
-- 不做外键指向 shop_orders：order_not_found 这一类恰恰是**没有**对应订单的，
-- 有外键就写不进来，而那正是最需要被记下的一种。
CREATE TABLE IF NOT EXISTS payment_anomalies (
  id              bigint      PRIMARY KEY,
  kind            text        NOT NULL
                              CHECK (kind IN ('amount_mismatch', 'order_not_found',
                                              'missing_transaction_id')),
  -- 哪条通道发现的。三个入口都会写，排查时要先知道是回调、主动查单还是扫描发现的
  source          text        NOT NULL CHECK (source IN ('notify', 'sync_pay', 'sweep')),

  order_no        text        NOT NULL,
  order_id        bigint,
  transaction_id  text        NOT NULL DEFAULT '',
  paid_cents      integer,
  -- order_not_found 时为空：我们根本没有那笔订单，也就无从谈「应付多少」
  expected_cents  integer,

  -- 处理记录。行不删——这是资金异常的台账，处理完也要留着备查
  resolved_at     timestamptz,
  resolved_by     text        NOT NULL DEFAULT '',
  resolve_note    text        NOT NULL DEFAULT '',

  created_at      timestamptz NOT NULL DEFAULT now()
);

-- 后台默认只看未处理的。部分索引：处理完的行不该拖慢这个查询
CREATE INDEX IF NOT EXISTS payment_anomalies_open_idx
  ON payment_anomalies (created_at DESC, id DESC) WHERE resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS payment_anomalies_order_no_idx
  ON payment_anomalies (order_no);
