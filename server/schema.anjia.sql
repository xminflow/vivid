-- 安家立业的表结构。库：anjia_dev（开发）。本文件可重复跑。
--
-- 与安东尼之家的 schema.sql 是**两个库两份文件**，不是一份文件里的两组表：
-- 需求文档 2.1 定了各小程序独立、不共享用户数据，所以 openid 单列唯一即可
-- （见 docs/adr/0001-单进程多库承载小程序矩阵.md）。
--
-- ============================================================
-- 用户表
-- ============================================================
-- 只建**登录必需**的列。安家立业的身份模型是三层——个人账号 / 企业认证账号 /
-- 会员（付费，权益是发起站内私信，见 CONTEXT.md）——但需求文档 4.3.2 里
-- 企业认证的「是否需营业执照」「审核方」都还是待确认，现在建就是猜一个需求方
-- 自己没定的东西。加列廉价，猜错了删列才贵，所以等认证真要做时再补。
--
-- 同理没有 status（封禁）：这一期没有任何入口能封人，建了也没人写。
--
-- 业务主键是雪花 ID（app/snowflake.py），不用 bigserial：不暴露注册量，
-- 将来合并矩阵数据时不撞主键。它超过 JS 的安全整数范围，出接口一律转字符串。
CREATE TABLE IF NOT EXISTS users (
  id            bigint      PRIMARY KEY,

  -- 微信身份。openid 是「本小程序内」的唯一标识，wx.login 换来的，是自然键
  openid        text        NOT NULL UNIQUE,
  -- 同一开放平台下跨应用的标识。本期用不上，先存着——二期要打通矩阵账号时，
  -- 没有历史 unionid 就补不回来了
  unionid       text,
  -- 解密手机号等加密数据要用，每次登录都会变。属于密钥，绝不出接口
  session_key   text        NOT NULL DEFAULT '',

  -- 自定义登录态。不拿 openid 当凭证：openid 泄出去就换不掉，token 能过期能吊销
  token             text UNIQUE,
  token_expires_at  timestamptz,

  -- 微信授权资料（用户点头像昵称填写时才有，可能一直是空）
  nickname      text        NOT NULL DEFAULT '' CHECK (length(nickname) <= 60),
  avatar_url    text        NOT NULL DEFAULT '',

  -- 会员。一期免费、用户自助开通、不分等级，只有是/否两种状态（需求文档 2.2）。
  -- 与企业认证完全正交：认证不附带会员，会员也不是申请认证的前提
  is_member         boolean NOT NULL DEFAULT false,
  member_since      timestamptz,
  -- 开通时写成一年后，但**一期任何地方都不校验它**，判断是不是会员只看 is_member。
  -- 这不是漏掉的校验，是有意为之——理由见 docs/adr/0002-会员到期时间只写不读.md。
  -- 它是二期收费唯一的历史基准线，删了就补不回来了
  member_expires_at timestamptz,

  created_at    timestamptz NOT NULL DEFAULT now(),
  last_login_at timestamptz,
  -- 登录次数。判断「这个号是不是只进来过一次」最省事的一列
  login_count   integer     NOT NULL DEFAULT 0
);

-- 上面的 CREATE TABLE 对已经建好的库不生效，会员那三列得单独补。
-- 带默认值加列在 PG 11+ 不重写全表，存量行直接读到 false，旧版本代码不引用这几列，可直接回滚
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_member         boolean NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS member_since      timestamptz;
ALTER TABLE users ADD COLUMN IF NOT EXISTS member_expires_at timestamptz;

-- 鉴权每个请求都要按 token 查一次，唯一约束已经带索引，这里不再重复建。
-- 过期清理暂时没有：token 有效期 30 天，量级还不值得起一个定时任务。

-- ============================================================
-- 企业认证申请
-- ============================================================
-- 这是一张**记录表**，不是用户表上的几个字段：一个用户可以有多条（被驳回后改完
-- 重交、被撤销后再申请都各是一条新记录），构成完整的申请历史。管理员据此看得出
-- 「这个人改过三次公司名」，那本身是风控信号，合并成一行就丢了。
--
-- 「是不是企业认证账号」因此是**查出来的**而不是存出来的：有一条 approved 就是。
-- 用户表上没有对应的冗余列——有了就得在撤销时记得改第二个地方，迟早漏。
--
-- 只收公司全称 + 联系人 + 手机号，不收营业执照：需求文档 4.3.2 明确不做严格资质
-- 核验，而一旦收取就要承担存储与核验责任，收了不看比不收更糟。
CREATE TABLE IF NOT EXISTS company_certifications (
  id            bigint      PRIMARY KEY,
  -- 用户没了，他的申请记录也没有存在的意义
  user_id       bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,

  company_name  text        NOT NULL CHECK (length(company_name) BETWEEN 2 AND 60),
  contact_name  text        NOT NULL CHECK (length(contact_name) BETWEEN 1 AND 20),
  -- 与安东尼之家 schema.sql 的三处 CHECK、app/models.py 同一条正则
  contact_phone text        NOT NULL CHECK (contact_phone ~ '^1[3-9][0-9]{9}$'),

  -- pending 待审核 / approved 已通过 / rejected 已驳回 / revoked 已撤销。
  -- 与 app/anjia/certifications.py 的 STATUSES 逐字一致
  status        text        NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'approved', 'rejected', 'revoked')),
  -- 驳回时必填，由接口层保证；其余状态是空串
  reject_reason text        NOT NULL DEFAULT '' CHECK (length(reject_reason) <= 200),

  -- 审核人存**管理员用户名的文本快照**，不存 ID、不建外键：管理员账号在安东尼之家
  -- 的库里（docs/adr/0001），跨库建不了外键；存 ID 则管理员一被删就变成一个查不到
  -- 的数字。语义与订单行的商品快照一致——记的是「审核发生的那一刻，是谁」
  reviewed_by   text        NOT NULL DEFAULT '',
  reviewed_at   timestamptz,

  created_at    timestamptz NOT NULL DEFAULT now()
);

-- 「一个账号最多一条生效认证」交给数据库，不交给应用层的先查再写——后者在并发下
-- 会漏。撤销时把那行改成 revoked，索引自动为下一次申请让路
CREATE UNIQUE INDEX IF NOT EXISTS company_certifications_one_approved
  ON company_certifications (user_id) WHERE status = 'approved';

-- 同理，同时最多一条待审：防连点提交产生两条待审记录，让管理员审到一个不知道该
-- 以哪条为准的队列
CREATE UNIQUE INDEX IF NOT EXISTS company_certifications_one_pending
  ON company_certifications (user_id) WHERE status = 'pending';

-- 公司全称上**没有**唯一约束：同名公司、分公司真实存在，而这一期不做资质核验，
-- 系统没有任何依据判定谁是李鬼。重名交给管理员在后台按公司名搜出来人工把关。

-- 后台队列按提交时间倒序翻页
CREATE INDEX IF NOT EXISTS company_certifications_created_at_idx
  ON company_certifications (created_at DESC);
-- 「我的最新一条」与「这个人的历史」都按用户查
CREATE INDEX IF NOT EXISTS company_certifications_user_idx
  ON company_certifications (user_id, created_at DESC);

-- ============================================================
-- 案例（首页内容流）
-- ============================================================
-- 一条案例 = 标题 + 图集 + 正文，只有企业认证账号能发，人工先审后发
-- （需求文档 2.3.1 / AJ-01）。
--
-- 与上面的 company_certifications 形状**刻意相反**：那是一张记录表（改完重交
-- 就是新一条，状态查出来），这里是一行会流转状态的实体（改完重交还是同一行）。
-- 不是抄漏了，理由见 docs/adr/0004-案例是状态实体而非提交记录.md。
CREATE TABLE IF NOT EXISTS cases (
  id            bigint      PRIMARY KEY,
  -- 作者。用户没了，他的案例也没有存在的意义
  user_id       bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,

  title         text        NOT NULL CHECK (length(title) BETWEEN 1 AND 30),
  -- 正文选填：企业发的是设计案例/新品/样板间，图是主体、字是配角
  body          text        NOT NULL DEFAULT '' CHECK (length(body) <= 1000),

  -- 图集。元素是 {"key":"static/anjia-case/…","w":1200,"h":1600} 三件套，
  -- **不是**裸 key 数组（与安玺·集 shop_products.images 的区别就在这里）：
  -- 双列瀑布流必须在图片加载**之前**就知道封面多高，否则页面先塌成空白再被
  -- 内容顶开。宽高不单开两列，是因为作者随时可能删掉第一张换封面，那样就得
  -- 记着同步改第二个地方，迟早漏。宽高由服务端在中转上传时读文件头解析，
  -- 不收客户端自报的值。
  --
  -- 落 static/ 前缀（公开可读、地址稳定、能命中微信图片缓存），不落 uploads/：
  -- 后者一律现签，签名 URL 每次都变，缓存全部落空（见 app/cos.py 的
  -- SIGN_WINDOW_SECONDS 与 build_static_key）。代价是没过审的图只要 key 泄漏
  -- 也能打开——key 是 uuid，不可枚举，且未过审的案例不出现在任何列表接口里。
  images        jsonb       NOT NULL DEFAULT '[]'::jsonb
                            CONSTRAINT cases_images_shape
                            CHECK (jsonb_typeof(images) = 'array'
                                   AND jsonb_array_length(images) BETWEEN 1 AND 9),

  -- pending 待审核 / published 已发布 / rejected 已驳回 / delisted 已下架。
  -- 与 app/anjia/cases.py 的 STATUSES 逐字一致。
  -- 删除**不在**这个集合里，它是下面的 deleted_at——一条案例可能在待审时被删、
  -- 也可能在已发布时被删，塞进 status 就把「删之前它是什么」抹掉了
  status        text        NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'published', 'rejected', 'delisted')),

  -- 驳回时必填，由接口层保证。存的是**预设选项的文案快照**而不是选项 code：
  -- 预设项将来会增删改，存 code 的话旧记录会指向一个已经不存在的选项，
  -- 语义与 reviewed_by 存用户名、订单行存商品快照一致
  reject_reason text        NOT NULL DEFAULT '' CHECK (length(reject_reason) <= 60),
  -- 管理员在预设选项之外补的话，同样原样回传给作者
  reject_note   text        NOT NULL DEFAULT '' CHECK (length(reject_note) <= 200),

  -- 审核人存管理员用户名的文本快照，不存 ID、不建外键：管理员账号在安东尼之家
  -- 的库里（docs/adr/0001），跨库建不了外键。同 company_certifications.reviewed_by
  reviewed_by   text        NOT NULL DEFAULT '',
  reviewed_at   timestamptz,

  -- 首次审核通过的时刻，也是首页内容流的排序锚点。
  --
  -- ⚠️ 一期首页按它倒序，**不按 views 排**——这与需求文档 AJ-02 写的「按浏览量等
  -- 热度指标排序」不一致，是有意偏离：冷启动阶段总共十几条案例，热度排序的结果
  -- 基本等于随机，还会让新发布的内容永远沉底。要改回热度排序只需换一行 ORDER BY，
  -- 所以没为它单开 ADR。
  --
  -- 作者编辑已发布的案例会把 status 打回 pending，但这一列**不清空**：再次过审
  -- 后它回到原来的位置，不会因为改了个错别字就重新抢一次首页头部
  published_at  timestamptz,

  -- 有多少**人**看过（不是看了多少次）。一期不参与排序，只作为二期做热度排序时
  -- 唯一的历史基准线——与会员到期时间「只写不读」是同一类取舍（docs/adr/0002）。
  -- 去重靠下面的 case_views，这一列是避免首页每条都 count(*) 的冗余计数
  views         integer     NOT NULL DEFAULT 0,

  -- 软删。硬删之后「这条曾经存在、是谁发的、何时删的」就查不到了，
  -- 而运营被监管问询时要的正是这段。非空即视为不存在，前后台一律不出
  deleted_at    timestamptz,

  created_at    timestamptz NOT NULL DEFAULT now(),
  -- 作者最后一次编辑的时刻。「审核通过后又被改过没有」只靠它跟 reviewed_at 比
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- 首页内容流。游标分页按 (published_at, id) 走，不用 offset——内容流一直有新条目
-- 插到最前，offset 翻第二页时会重复或漏条。做成**部分索引**：前台只认已发布且
-- 未删除的，条件写进索引，扫的行数就等于要出的行数
CREATE INDEX IF NOT EXISTS cases_feed_idx
  ON cases (published_at DESC, id DESC)
  WHERE status = 'published' AND deleted_at IS NULL;

-- 后台四个分页各自按提交时间倒序翻页
CREATE INDEX IF NOT EXISTS cases_status_created_at_idx
  ON cases (status, created_at DESC);

-- 「我的发布」：一个作者的全部案例混排，带状态角标
CREATE INDEX IF NOT EXISTS cases_user_idx
  ON cases (user_id, created_at DESC);

-- 「同一账号待审最多 5 条」的护栏每次发布都要数一遍。这里不做成唯一约束——
-- 上限是条软规则（阈值会调），交给接口层按这个索引计数
CREATE INDEX IF NOT EXISTS cases_user_pending_idx
  ON cases (user_id)
  WHERE status = 'pending' AND deleted_at IS NULL;

-- ============================================================
-- 案例浏览
-- ============================================================
-- 浏览量去重用的明细。存在的唯一理由是让 cases.views 的语义是「多少人看过」
-- 而不是「被点开多少次」：二期拿它排「哪条案例更值得推」，本质是触达了多少人，
-- 同一个人反复刷不该把一条内容顶上去——在只有十几个企业账号的冷启动阶段，
-- 作者自己每天点开看一眼就能刷榜。
--
-- 永久去重，不按天：一个人对一条案例只留一行。作者看自己的案例不写这张表，
-- 管理员在后台预览走的是另一个接口，也不碰这里。
--
-- 计数是一条 SQL：INSERT ... ON CONFLICT DO NOTHING RETURNING，命中了才
-- UPDATE cases SET views = views + 1，两句放进同一个 CTE，并发下不会重复加。
CREATE TABLE IF NOT EXISTS case_views (
  -- 复合主键即去重约束，不另建自增 ID：这张表除了「这个人看过这条」之外
  -- 不承载任何信息，一个代理键只会多一列和多一个索引
  case_id     bigint      NOT NULL REFERENCES cases (id) ON DELETE CASCADE,
  user_id     bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  created_at  timestamptz NOT NULL DEFAULT now(),

  PRIMARY KEY (case_id, user_id)
);

-- 没有单独按 user_id 的索引：一期没有「我看过的案例」这个功能，
-- 而外键的级联删除走的是主键前缀之外的列，量级还不值得为它多维护一个索引。
-- 表按「案例数 × 看过的人数」增长；真涨起来了按 created_at 归档旧行即可，
-- 不影响 cases.views——那一列是独立累加的，不是从这张表算出来的
