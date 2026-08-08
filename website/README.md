# 小程序矩阵 · 管理后台

React + TypeScript + Tailwind CSS v4 + shadcn/ui，包管理用 pnpm。

数据全部来自仓库里的 `server/`（FastAPI），本工程只有界面，没有自己的后端。

当前实现：安东尼之家的**展厅预约申请**与**服务申请**两个列表（查看、筛选、分页、详情），
以及**首页图片**（小程序首页三组配图的上传、排序、替换）。

## 起服务

后端要先跑起来（3000 端口，见 `server/README.md`）：

```bash
cd ../server
uv run uvicorn app.main:app --host 0.0.0.0 --port 3000
```

然后：

```bash
pnpm install
pnpm dev        # http://localhost:5190
```

`vite.config.ts` 把 `/api/admin` 代理到 `http://127.0.0.1:3000`。后端没起时页面会弹
「连不上后台服务」，不会静默显示成空列表。

```bash
pnpm build      # tsc -b && vite build，改完代码至少要过这一步
pnpm lint       # oxlint
```

## 目录结构

前端按 feature 分目录，跨 feature 复用的才放到 `src/components`：

```
src/
  main.tsx                     路由与全局挂载
  app-shell.tsx                左侧导航 + 内容区
  components/ui/               shadcn 生成的基础组件，尽量不手改
  components/                  本项目自己的通用组件（分页条、筛选下拉、状态徽标…）
  features/antony/             安东尼之家
    api.ts                     接口调用，只认 {ok:true} 才算成功
    types.ts                   接口返回类型，与 server/app/admin.py 对应
    constants.ts               选项与字段中文名字典
    use-paged-list.ts          列表页公共逻辑（取数、翻页、筛选、报错）
    appointments-page.tsx      展厅预约申请
    service-applications-page.tsx  服务申请
    home-media-page.tsx        首页图片
```

再接别的小程序时，在 `src/features/` 下另起一个目录，在 `app-shell.tsx` 的 `NAV` 里加一组。

## 两个必须知道的约定

**选项值三处同步。** `constants.ts` 里的来者身份、预约需求、服务类别、状态，必须与
`server/schema.sql` 的 CHECK、`server/app/models.py`、`antony-casa/` 小程序里的常量逐字一致。
对不上时页面会把原始值显示出来——不会崩，但运营看到的是英文 id。

**表单字段中文名是小程序定义的镜像。** 服务申请的 `fields` 是 jsonb，键是字段 id，
服务端不认识它们的含义。`constants.ts` 的 `FIELD_LABELS` / `UPLOAD_LABELS` 抄自
`antony-casa/mock/service.js`，小程序加字段时这里要跟着加。

**首页图片是「先上传、后保存」两步。** 选完图立刻传到 COS 拿对象键，但要点「保存」
才会整组写进配置。好处是运营可以先传几张、调完顺序再落地，中途关掉页面不会把线上首页
改成一半的样子；代价是传了不保存的图会留在桶里成为孤儿（清理办法见 `server/README.md`
的「首页配图」）。**清空某一组不等于首页那块不显示**，而是回到小程序里写死的兜底图。

## 还没做的

- **鉴权**。`/api/admin` 现在是裸奔的，任何人都能拉走全部客户姓名和手机号；
  自从有了首页图片的写接口，任何人还能直接换掉线上小程序的首页配图。
  只能在本机或内网用，上线前必须先加，见 `server/README.md` 的「上线前要做的」。
- 状态流转（标记已联系 / 已到店 / 已取消）、导出、商品模块。
