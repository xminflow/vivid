# 安玺·集 购物板块设计

日期：2026-08-10
涉及：`antony-casa/`（小程序）、`server/`（FastAPI）、`website/`（管理后台）

术语以根目录 `CONTEXT.md` 的「安玺·集」与「交易」两节为准，本文不重复定义。

## 背景

安玺·集是 `antony-casa` 小程序内新增的购物板块，占据一个 tab 入口，用户在其中浏览商品、
加入购物车、填写收货地址、下单并**通过微信支付真实付款**，运营在管理后台上架商品与处理订单。

这是矩阵内**第一个具备真实交易能力的板块**，与 `需求文档_小程序矩阵_20260725.md` 第 1.4 节
「不接在线支付 —— 四个小程序均无交易能力」直接冲突。该约束经确认作废，需求文档需同步修订。
第 1.3 节「一个小程序只解决一件事」的原则同样在此让位。

仓库现状：三层（小程序 / 服务端 / 后台）都**没有任何**商品、订单、支付、购物车相关代码，
本设计全部是新建。

## 一、已确认的产品决策

| # | 决策 | 选择 | 代价 |
|---|---|---|---|
| 1 | 承载小程序 | `antony-casa`，tab 插到第 3 位：首页 / 服务 / 安玺·集 / 我的 | 违反「一个小程序只解决一件事」 |
| 2 | 交易形态 | 真实微信支付 | 需商户号、经营类目、APIv3 密钥（已确认齐备） |
| 3 | 商品规格 | 无 SKU，一个商品一个价格 | 同款不同色需建成多个商品 |
| 4 | 参数 | 自由名值对列表，≤20 条 | 运营录入不统一，靠规范约束 |
| 5 | 图片 | 单一图集，第一张即封面 | 列表页与详情页共用同一构图 |
| 6 | 详情 | 纯文本简介 + 详情图序列，不用富文本 | 详情排版依赖出图 |
| 7 | 履约 | 物流发货 | 需地址簿、发货、物流回传 |
| 8 | 运费 | 全场包邮，运费恒为 0 | 运费成本并进售价 |
| 9 | 收货地址 | 自建地址簿，≤20 条 | 不用 `wx.chooseAddress` |
| 10 | 库存 | 不记录库存，只有在售/下架 | **超卖必然发生**，靠人工退款兜底 |
| 11 | 分类 | 单层，商品必属一个分类，只能停用不能删除 | 停用会批量下架其下商品且不可逆 |
| 12 | 搜索 | 仅匹配商品标题 | 简介与参数搜不到 |
| 13 | 购物车 | 有，存服务端，≤50 行 | 需处理失效条目 |
| 14 | 订单可分性 | **不可分**：整单发货、整单收货、整单退款 | 分批到货只能让用户下两单 |
| 15 | 订单状态 | 待付款 → 待发货 → 待收货 → 已完成；旁支 已关闭 / 已退款 | |
| 16 | 关单 | 超时 30 分钟自动关；用户仅在待付款时可自助取消 | |
| 17 | 确认收货 | 发货后 10 天自动置为已完成 | |
| 18 | 退款 | 仅超级管理员可发起，强制填原因，整单原路退回 | 用户无法自助申请 |
| 19 | 售后入口 | 后台可换的一张**客服二维码图**，长按识别加微信 | 小程序内无任何在线沟通能力 |
| 20 | 发票 | 不做，线下开 | |
| 21 | 商品删除 | 被**任何**订单引用即不可删（数据库 `ON DELETE RESTRICT`） | 卖过的商品永远只能下架 |
| 22 | 通知 | 只做「发货通知」一条订阅消息，提交订单时请求授权 | 支付成功不另行通知 |
| 23 | 权限 | 商品/分类/订单/发货：普通管理员；退款：仅超管 | 复用现成 `current_super`，不加 role 列 |

### 不做的事

购物车合单后的部分退款、分批发货、SKU 规格、库存扣减、优惠券、会员价、划线价、
运费模板、七天无理由自助退、退款申请工单、电子发票、多商家入驻与分账、
微信原生客服会话、商品收藏、评价晒单、销量展示。

## 二、数据模型

新增 8 张表，全部走 `migrations/008_shop.sql` + `008_rollback.sql`，同步更新 `schema.sql`。
实体表主键一律雪花 ID（`app/snowflake.py`），响应中序列化为字符串。
**金额一律用整数「分」**存储与传输。

### `shop_categories` 分类

`id` 雪花 PK · `name` ≤20 唯一 · `sort_order` int（越大越前）·
`status` CHECK `('active','disabled')` · `created_at` / `updated_at` + `touch_updated_at` 触发器

### `shop_products` 商品

`id` 雪花 PK · `category_id` → `shop_categories(id)` NOT NULL ·
`title` ≤60 · `summary` ≤500 · `price_cents` int CHECK `> 0` ·
`images` jsonb（COS key 数组，≤10，`[0]` 为封面）·
`detail_images` jsonb（≤20）· `params` jsonb（`[{name, value}]`，有序，≤20）·
`status` CHECK `('active','off')` DEFAULT `'off'` · `sort_order` int ·
时间戳 + 触发器。索引：`(status, sort_order DESC, created_at DESC)`、`(category_id)`

搜索用 `title ILIKE '%kw%'`，不建全文索引——预计商品量在百件级。

### `shop_addresses` 收货地址

`id` 雪花 PK · `user_id` → `users(id)` ON DELETE CASCADE ·
`receiver` ≤20 · `phone` CHECK 手机号正则（复用 `users.phone` 的写法）·
`province` / `city` / `district` / `detail` ≤100 · `is_default` bool ·
时间戳。`(user_id)` 索引；`(user_id) WHERE is_default` 部分唯一索引保证每人至多一个默认地址。

### `shop_cart_items` 购物车

`id` 雪花 PK · `user_id` → `users(id)` CASCADE · `product_id` → `shop_products(id)` **CASCADE** ·
`quantity` int CHECK `1..99` · 时间戳。`UNIQUE (user_id, product_id)`——重复加入即累加数量。

购物车只存 ID 与数量，**每次读取实时回查**当前价格与在售状态；下架商品置灰不可勾选。

### `shop_orders` 订单

- 标识：`id` 雪花 PK · `order_no` text UNIQUE · `user_id` → `users(id)`
- 状态：`status` CHECK `('pending_pay','pending_ship','pending_receive','completed','closed','refunded')`
- 金额：`total_cents` int CHECK `> 0`
- **地址快照**：`receiver` / `phone` / `province` / `city` / `district` / `detail`，全部 NOT NULL。
  不存 `address_id` 引用——用户改地址不能改变历史订单。
- 支付：`transaction_id` text（微信支付订单号，非空时唯一）· `paid_at`
- 发货：`shipping_type` CHECK `('express','local','none')` · `shipping_company`（微信标准编码）·
  `tracking_no` · `shipped_at`
- 收货：`received_at`
- 关单：`closed_at` · `close_reason` CHECK `('timeout','user_cancel')`
- 退款：`refunded_at` · `refund_reason` · `refund_id`（微信退款单号）
- 运营：`remark`
- 时间戳 + 触发器。索引：`(user_id, created_at DESC)`、`(status, created_at DESC)`、
  `(transaction_id) WHERE transaction_id IS NOT NULL` 唯一

### `shop_order_items` 订单行

`id` 雪花 PK · `order_id` → `shop_orders(id)` ON DELETE CASCADE ·
`product_id` → `shop_products(id)` **ON DELETE RESTRICT** ·
`title_snapshot` · `price_cents_snapshot` · `image_snapshot`（COS key）· `quantity`

`ON DELETE RESTRICT` 就是决策 21 的执行点——「被订单引用的商品不可删」由数据库强制，
不依赖应用层记得检查。后台删除前会先查出引用它的订单号清单展示给运营。

订单行金额**永远取自快照**，任何场景下都不回查 `shop_products` 的当前价格。

### `shop_order_seq` 单号序号

`day` date PK · `next` int NOT NULL

单号格式 `AX + YYYYMMDD + 6 位序号`（如 `AX20260810000137`），用单条
`INSERT ... ON CONFLICT (day) DO UPDATE SET next = shop_order_seq.next + 1 RETURNING next`
原子取号，并发安全、不依赖应用层锁；`order_no` 的唯一索引兜底。
该单号同时作为微信支付的 `out_trade_no`（微信要求 6–32 位、商户内唯一，正好满足）。

### `shop_settings` 板块设置

`id` smallint PK CHECK `(id = 1)` · `service_qr_key` text（客服二维码 COS key）· `updated_at`

单行配置表。

## 三、接口

### 小程序端 `/api/shop/*`

浏览类走 `requestOptionalAuth`，其余一律需要登录（`antony-casa` 启动即静默登录，实际都有 token）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/shop/categories` | 启用中的分类，按 `sort_order` |
| GET | `/api/shop/products` | 在售商品分页，参数 `categoryId` / `keyword` / `page` / `pageSize` |
| GET | `/api/shop/products/{id}` | 商品详情；下架商品返回 404 |
| GET | `/api/shop/settings` | 客服二维码 URL |
| GET | `/api/shop/cart` | 购物车，每行附当前价格与在售状态 |
| POST | `/api/shop/cart` | 加入购物车 `{productId, quantity}` |
| PUT | `/api/shop/cart/{id}` | 改数量 |
| DELETE | `/api/shop/cart/{id}` | 删除一行 |
| DELETE | `/api/shop/cart/invalid` | 一键清理失效条目 |
| GET | `/api/shop/addresses` | 地址列表 |
| POST/PUT/DELETE | `/api/shop/addresses[/{id}]` | 地址增改删 |
| PUT | `/api/shop/addresses/{id}/default` | 设为默认 |
| POST | `/api/shop/orders` | 下单：`{addressId, source: 'cart' \| 'direct', items}`，返回订单与支付参数 |
| GET | `/api/shop/orders` | 我的订单，`status` 筛选 + 分页 |
| GET | `/api/shop/orders/{id}` | 订单详情 |
| POST | `/api/shop/orders/{id}/pay` | 待付款订单重新拉起支付 |
| POST | `/api/shop/orders/{id}/sync-pay` | 支付返回后主动向微信查单（掉单兜底） |
| POST | `/api/shop/orders/{id}/cancel` | 用户取消，仅限待付款 |
| POST | `/api/shop/pay/notify` | 微信支付回调，**无鉴权**，验签 + 幂等 |

### 管理端 `/api/admin/shop/*`

新建 `server/app/shop_admin.py`，`APIRouter(prefix="/api/admin/shop", dependencies=[Depends(current_admin)])`，
在 `main.py` 注册。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST/PUT | `/categories[/{id}]` | 分类列表（附商品数）/ 新建 / 编辑 |
| PUT | `/categories/{id}/status` | 停用/启用；停用时级联下架其下在售商品 |
| POST | `/categories/{id}/activate-products` | 批量上架该分类下的商品 |
| GET/POST/PUT/DELETE | `/products[/{id}]` | 商品增删改查；删除受 `ON DELETE RESTRICT` 约束 |
| PUT | `/products/{id}/status` | 上架 / 下架 |
| POST | `/products/upload` | 图片上传，raw bytes（照抄 `home-media/upload`），落 `static/` 前缀 |
| GET | `/orders` | 订单列表，筛选 `status` / `keyword`（单号或收件人）/ 日期区间 + 分页 |
| GET | `/orders/{id}` | 订单详情 |
| POST | `/orders/{id}/ship` | 发货 `{shippingType, company, trackingNo}` |
| PUT | `/orders/{id}/ship` | 修改运单号，记修改日志并重新回传微信 |
| POST | `/orders/{id}/refund` | 退款 `{reason}`，**`current_super` 保护** |
| PUT | `/orders/{id}/remark` | 后台备注 |
| GET/PUT | `/settings` | 客服二维码 |

## 四、微信支付链路

### 需要的配置（`server/.env`）

```
WXPAY_MCHID=            # 商户号
WXPAY_APIV3_KEY=        # APIv3 密钥
WXPAY_CERT_SERIAL=      # 商户证书序列号
WXPAY_PRIVATE_KEY=      # 商户 API 私钥
WXPAY_NOTIFY_URL=https://antonycasa.weelume.com/api/shop/pay/notify
```

配置缺失时**服务启动不失败**，但下单接口返回明确错误——与 COS 未配置时的处理一致，
让不涉及交易的功能仍可开发和运行。

### 下单到支付

1. `POST /api/shop/orders` — 服务端逐行校验商品**在售**并以当前价格计算总额（绝不信任客户端传来的价格）
2. 取号建单：`shop_orders`（`pending_pay`）+ `shop_order_items` 快照，同事务
3. 调微信 JSAPI 下单，`openid` 取自 `users` 表（客户端从不接触 openid）
4. 返回 `{orderId, orderNo, payParams}`，小程序 `wx.requestPayment`
5. 若来源是购物车，**支付成功后**才清空对应购物车行

### 支付确认（双通道）

- **回调**：`POST /api/shop/pay/notify`，验签 + 解密 → 幂等（`transaction_id` 唯一索引，
  已处理的通知直接返回成功）→ `pending_pay` 置为 `pending_ship`
- **主动查单**：小程序把 `wx.requestPayment` 成功视为「已提交」而非「已支付」，随后**轮询**
  `GET /api/shop/orders/{id}`（参照 weelume-base：8 次 × 1.2 秒，约 10 秒）。服务端在被查询时，
  若订单仍为 `pending_pay` 且距上次查微信超过 30 秒，就顺带向微信查一次单并复用同一段状态迁移代码。
  为此 `shop_orders` 增加 `last_query_at` 列做节流。

两条通道走同一段状态迁移代码，任一先到都生效。轮询耗尽仍未支付时，小程序提示
「支付处理中，请稍后刷新」而非报失败——钱可能已经扣了。

### 发货信息录入

微信对小程序实物交易要求支付后在时限内回传物流信息，否则订单被判异常、影响结算。
后台发货成功后即调该接口上传。**发货方式开放三档**：快递发货（物流公司编码 + 运单号）、
同城配送、无需物流——卖家具走专线或自送时没有运单号，这两档是刚需。
物流公司列表内置一份常用编码硬编码在 `server/app/wxpay.py`，不做动态拉取。

**动工前需查证**：发货信息录入的当前接口版本、时限、以及「同城配送 / 无需物流」两档的确切参数要求。

### 退款

超管在后台点退款 → 填原因（必填）→ 调微信退款接口 → 订单置 `refunded`，记 `refund_id` 与原因。
只做整单退，不做部分退。

### SDK 选型与新增依赖

**微信支付官方 SDK 只有 Java / PHP / Go 三种**（`wechatpay-apiv3` 组织），没有官方 Python SDK。
该组织下的 `wechatpay-skills` 是给 AI Agent 用的技能包，不是可 import 的库。

Python 侧采用第三方事实标准 **`wechatpayv3`**（minibear2021 维护，当前 2.0.3）：

```
install_requires = ["requests>=2.21.0", "cryptography>=35.0.0"]
extras_require   = {"async": ["httpx>=0.28.1", "aiofiles>=23.2.0"]}
```

**不装 `[async]` extra**，把 SDK 的阻塞调用包进 `run_in_threadpool` —— 这是同厂
`D:\code\weelume-base` 的既有做法，也与本仓库 `admin_auth.py` 用
`anyio.to_thread.run_sync` 跑 scrypt 的处理一致。净新增 `wechatpayv3` + `requests` +
`cryptography` 三个依赖。

### 参考实现：`D:\code\weelume-base`

同厂后端（FastAPI + PostgreSQL）已有四条微信支付链路，直接照抄以下做法：

- **SDK 只用 4 个方法**：`pay` / `query` / `callback` / `sign`，全部包在 `run_in_threadpool` 里
- **验签方式两种模式都支持**：「平台证书」（`cert_dir`）与「微信支付公钥」（`public_key` +
  `public_key_id`）由配置决定，两者都未配置时报明确错误。故本项目**无需事先确定商户号是哪种模式**
- **PEM 归一化**：私钥/公钥可以以单行形式写在 `.env` 里（去 BEGIN/END 标记、还原 `\n`、剥引号）
- **`wx.requestPayment` 的 `paySign` 是唯一自己签的东西**，用 `wxpay.sign([appid, timeStamp, nonceStr, package])`
- **回调幂等**：`SELECT ... FOR UPDATE` 锁订单行 → 已支付直接返回 → 校验金额与
  `payer_openid` 是否与订单一致 → 再迁移状态。`transaction_id` 的唯一索引是数据库层第二道防线
- **密钥不入日志**：它有一条专门的测试断言密钥与 apiv3 secret 不出现在日志里，一并抄

**它没有实现、我们必须自己写的三块**：超时关单（`wxpay.close()` 在那边从未被调用）、
发货信息录入、退款。这三项无现成参考。

**为什么不手写**：手写只需 `cryptography` 一个依赖，且风格与 `cos.py` 手写 COS Signature v5 一致，
更贴合本仓库拒绝 `python-multipart` / `bcrypt` / `cos-sdk` 的一贯取向。但**回调验签写错的后果是
他人可伪造「支付成功」通知白拿走货**，而 COS 签名写错最多是上传失败——两者不该用同一标准衡量。
手写还需自行处理平台证书的定期轮换与缓存。此项经确认选择用库。

无论用库还是手写，`cryptography` 都无法回避：RSA-SHA256 是 APIv3 的硬要求，Python 标准库没有 RSA。

**待查证（阶段三前）**：商户号的验签方式是「平台证书」还是「微信支付公钥」模式。
两者的配置项与证书轮换需求不同，需从商户平台后台确认。

## 五、定时任务

`server/app/main.py` 的 lifespan 内起一个 asyncio 循环，每 60 秒扫一次：

1. **超时关单**：`pending_pay` 且创建超过 30 分钟 → 先调微信关闭订单接口（防止用户在关单后仍支付成功），
   再置 `closed` / `close_reason='timeout'`
2. **自动确认收货**：`pending_receive` 且 `shipped_at` 超过 10 天 → 置 `completed`

批次领取用 `SELECT ... FOR UPDATE SKIP LOCKED`（照抄 weelume-base 的
`sweep_pending_orders`），**多副本天然协作，不需要分布式锁**；配合幂等的
`UPDATE ... WHERE status = ...`，重复执行也无害。

`shop_orders.last_query_at` 兼作扫描节流字段，与主动查单共用。

## 六、小程序页面

`antony-casa/app.json` 的 `pages` 新增 8 项，`tabBar.list` 第 3 位插入安玺·集。
每页四文件、`"usingComponents": {}`、样式只用 `styles/tokens.wxss` 的 token，
一律经 `utils/api.js` 请求，遵循仓库既有约定。

| 页面 | 内容 |
|---|---|
| `pages/shop/shop` | **tab 入口**。搜索框 + 分类横滑 + 商品列表（封面/标题/价格），上拉加载 |
| `pages/product/product` | 图集轮播、标题、价格、简介、参数表、详情图序列、加入购物车、立即购买、客服 |
| `pages/cart/cart` | 勾选、改数量、删除、失效条目置灰与一键清理、合计、去结算 |
| `pages/checkout/checkout` | 选地址、订单行、合计、提交订单并拉起支付、请求发货通知订阅授权 |
| `pages/orders/orders` | 我的订单，tab：全部 / 待付款 / 待发货 / 待收货 |
| `pages/order/order` | 订单详情：状态、订单行快照、地址、单号、物流信息、取消、客服 |
| `pages/address/address` | 地址簿列表、设默认、删除 |
| `pages/address-edit/address-edit` | 地址新增 / 编辑，省市区用 `picker` 的 `region` 模式 |

`pages/mine/mine` 新增「我的订单」与「收货地址」两个入口。

## 七、管理后台页面

新增到 `website/src/features/antony/`（安玺·集属于 antony-casa 小程序），
`app-shell.tsx` 的「安东尼之家」NAV 组下加 4 项，`main.tsx` 加 4 条路由。
沿用仓库既有约定：kebab-case 文件名、具名导出、`usePagedList`、shadcn 组件、
`toast` 反馈、`confirm()` 二次确认。

| 页面 | 关键形态 |
|---|---|
| `shop-products-page.tsx` | 分页列表（封面缩略图 / 标题 / 分类 / 价格 / 状态 / 排序值），筛选分类与状态，关键词搜标题。新建与编辑用整页表单：标题、分类下拉、价格、简介、**图集上传**（顺序调整、≤10）、**详情图上传**（≤20）、**参数名值对**（增删行、调顺序、≤20）、排序数字。新建后默认「下架」。删除时先请求引用它的订单号清单，有引用则禁用删除并列出单号 |
| `shop-categories-page.tsx` | 非分页列表：名称、排序值、状态、**该分类下商品数**。停用时二次确认写明「将同时下架 N 件在售商品，重新启用不会自动恢复」。提供「批量上架该分类商品」按钮 |
| `shop-orders-page.tsx` | 分页列表：单号、下单时间、收件人、金额、状态。筛选状态 / 日期区间，关键词搜单号或收件人。行点开抽屉：订单行快照、地址、支付信息、物流信息、备注。操作：**发货**（方式三选一 + 物流公司下拉 + 运单号）、**改运单号**、**备注**、**退款**（仅超管可见，必填原因，二次确认写明金额） |
| `shop-settings-page.tsx` | 客服二维码上传与预览 |

图片上传照抄 `home-media-page.tsx`：隐藏 `<input type="file">` + 按钮触发、
客户端预检大小与类型、**顺序上传**保序、`draft`/`saved` 对比算 `isDirty`、`beforeunload` 守卫。

## 八、明确接受的代价

1. **超卖必然发生**。不记库存 + 购物车 + 真支付 ⇒ 两个用户可以同时把最后一件沙发付款成功。
   唯一补救是运营发现后手动退款并道歉，且要承担微信支付的退款手续费。
2. **卖过的商品永远删不掉**，只能下架，后台商品列表会随时间单调增长。
3. **分类停用不可逆**地把其下商品改写为下架；重新启用需手动批量上架。
4. **售后完全依赖人工**：用户只能长按二维码加微信，小程序内无任何在线沟通与自助退款能力。
   需要有人真的盯着这个微信号。
5. **新增 3 个依赖**（`wechatpayv3` / `requests` / `cryptography`），引入了一个第三方维护者
   作为支付链路的信任方。这打破了仓库依赖极简的取向，换来的是不必自己实现回调验签与证书轮换，
   且与同厂 `weelume-base` 的选型一致。
6. **超时关单、发货信息录入、退款三块没有内部参考实现**，`weelume-base` 都没做过，
   这三项的风险与工作量高于其余部分。
7. **微信经营类目未落地则整个板块不可上线**，与代码质量无关。
8. **需求文档 1.3 / 1.4 两条一期约束被推翻**，文档需同步修订。

## 九、分期

| 阶段 | 内容 | 可独立验收的标志 |
|---|---|---|
| 一 | 数据表 + 分类与商品的后台管理 + 小程序端浏览（集首页 / 商品详情） | 运营能上架一件真实商品，用户在小程序里看到它 |
| 二 | 购物车 + 地址簿 + 下单（不接支付）+ 后台订单列表 | 能走完下单流程，订单出现在后台，状态停在待付款 |
| 三 | 微信支付接入：JSAPI 下单、回调、主动查单、超时关单 | 真实付款一笔并正确流转到待发货 |
| 四 | 发货、发货信息录入、退款、订阅消息、自动确认收货 | 全链路闭环 |

**先只做阶段一并交付验收**，确认形态无误后再往下推。
