# antony-web · 安东尼之家官网

面向客户的官网，内容与 `antony-casa/` 小程序同源：同一批文案、同一批实拍图、
同一套后端接口。发布在 <https://antonycasa.weelume.com>。

技术栈：Vite + React 19 + TypeScript + Tailwind v4 + pnpm，与后台管理 `website/` 一致。

## 和小程序的关系

内容一比一搬过来，但有三处**刻意的差异**，改之前先看清楚为什么：

| | 小程序 | 官网 | 为什么 |
|---|---|---|---|
| 「我的」页 | 有（会员资料、我的预约） | **没有** | 依赖 `wx.login` 静默拿 openid，浏览器里没有等价机制。做手机号+验证码登录要接短信服务，是另一个需求 |
| 省市区选择 | 原生 region 选择器 | 文本输入 | web 没有等价控件，做级联要额外带一份行政区划数据和新依赖，而服务端存的本来就是一个字符串 |
| 布局 | 移动端竖屏 | 响应式 | 手机端对齐小程序观感，桌面端重新排版（展厅六宫格、服务图文左右交替） |

其余四个页面（首页 / 服务 / 预约 / 服务申请）与小程序逐字一致。

**文案和表单定义有两份副本**，改一处必须同步另一处：

- `src/features/home/content.ts` ←→ `antony-casa/mock/home.js`
- `src/features/services/content.ts` ←→ `antony-casa/mock/service.js`
- `src/features/booking/content.ts` ←→ `antony-casa/pages/booking/booking.js` 的选项常量。
  这一组共**五处**：加上 `server/schema.sql` 的 CHECK、`server/app/models.py`，
  以及后台管理筛选下拉用的 `website/src/features/antony/constants.ts`

  ⚠️ 改这组值同时是**数据库变更**（`visitor_type` 上有 CHECK 约束），必须走
  `server/migrations/` 下的两步：先放宽约束到新旧并集 → 发布代码 → 回填并收紧。
  只改代码会让表单能选、库里存不进去。

## 开发

```bash
pnpm install
pnpm dev          # http://127.0.0.1:5180，/api 代理到本机 127.0.0.1:3000
```

要连线上后端（省去本地起服务）：

```bash
VITE_PROXY_TARGET=https://antonycasa.weelume.com pnpm dev
```

⚠️ 连线上意味着提交的表单会真的写进生产库。测试用 `1355` 开头的手机号，跑完删掉。

```bash
pnpm build        # tsc -b && vite build，产物在 dist/
pnpm lint
```

## 发布

产物由 `server/deploy/deploy.sh` 同步到服务器，Caddy 直接托管静态文件：

```powershell
cd D:\code\vivid\antony-web
pnpm build
```

```bash
wsl -d Ubuntu -- bash /mnt/d/code/vivid/server/deploy/deploy.sh --skip-build
```

两个站点共用一台机器、一个后端容器：

| 域名 | 内容 | 产物目录 |
|---|---|---|
| `antonycasa.weelume.com` | 官网（本项目） | `antony-web/dist` → `/srv/site` |
| `admin.antonycasa.weelume.com` | 后台管理 | `website/dist` → `/srv/admin` |

详见 `server/deploy/README.md`。

## 接口

官网只用三个不需要登录的接口，小程序那套 token 登录态在这里不存在：

| 用途 | 接口 |
|---|---|
| 提交展厅预约 | `POST /api/appointments` |
| 提交服务申请 | `POST /api/service-applications` |
| 换一个 COS 直传地址 | `POST /api/upload-url` |

前端一律用相对路径（`src/shared/config.ts` 里 `API_BASE = ''`），不拼域名——
线上官网和接口同域，由 Caddy 分流。

## 图片上传与 COS 的 CORS（已配好）

服务申请页的图片是**浏览器直传 COS** 的（服务端只签地址，不中转字节）。
小程序不受同源策略约束，所以这件事只在官网这边成立：**桶没配 CORS 规则时，
上传会在预检阶段就被浏览器拦掉**，控制台报 `No 'Access-Control-Allow-Origin' header`。

当前桶已经配好了，实测上传和后台看图都正常。**换桶、换域名或加新前端域名时要重新配**，
规则如下（腾讯云 COS 控制台 → 存储桶 → 安全管理 → 跨域访问 CORS 设置）：

| 项 | 值 |
|---|---|
| 来源 Origin | `https://antonycasa.weelume.com`（本地开发再加一行 `http://127.0.0.1:5180`） |
| 操作 Methods | `PUT`、`GET`、`HEAD` |
| Allow-Headers | `*` |
| Expose-Headers | `ETag` |
| 超时 Max-Age | `600` |

验证方式：在官网的服务申请页选一张图，或在浏览器控制台跑：

```js
const r = await fetch('/api/upload-url', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ scene: 'cors-probe', ext: 'png' }),
}).then(r => r.json())
await fetch(r.url, { method: 'PUT', headers: { 'Content-Type': 'application/octet-stream' },
  body: new Blob([new Uint8Array(8)]) }).then(r => r.status)   // 期望 200
```

没配 CORS 时，服务申请页的**文字部分照常能提交**，只有图片传不上去。

## 结构

```
src/
  shared/            请求、上传、配置、通用组件——不含业务语义
  layout/            顶栏 + 页脚
  features/
    home/            首页：首屏画廊、展厅六宫格、活动预告
    services/        服务列表 + 服务申请表单（数据驱动）
    booking/         展厅预约表单
```

表单是数据驱动的：`services/content.ts` 里的 `fields` 决定 `ApplyPage` 渲染什么，
加字段不用动页面代码。
