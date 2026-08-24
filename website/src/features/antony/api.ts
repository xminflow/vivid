// 安东尼之家后台接口。服务端是仓库里的 server/（FastAPI）。
// 开发时 vite 把 /api/admin 代理过去，见 vite.config.ts。
//
// 请求本身（登录态、401 处理、错误约定）在 @/lib/api，这里只列有哪些接口。

import { call, get } from '@/lib/api'
import type { PageParams, PagedResult } from '@/lib/paging'

import type {
  Appointment,
  AppointmentQuery,
  CategoryStatus,
  HomeMediaResult,
  HomeMediaUploadResult,
  HomeSlot,
  PaymentAnomaly,
  PaymentAnomalyQuery,
  ProductStatus,
  ServiceApplication,
  ServiceApplicationQuery,
  ShopCategory,
  ShopOrderDetail,
  ShopOrderQuery,
  ShopOrderRow,
  ShopOrderShipInput,
  ShopOrderTrackingInput,
  ExpressCompany,
  ShopProductDetail,
  ShopProductInput,
  ShopProductQuery,
  ShopProductRow,
} from './types'

export const listAppointments = (params: AppointmentQuery & PageParams) =>
  get<PagedResult<Appointment>>('/appointments', params)

export const listServiceApplications = (params: ServiceApplicationQuery & PageParams) =>
  get<PagedResult<ServiceApplication>>('/service-applications', params)

// 删除是不可撤销的，没有软删除也没有回收站。调用前必须二次确认，
// 两个列表页都在 onClick 里用 confirm() 挡了一道（与账号管理页一致）。
// 记录已被别人删掉时服务端回 404，@/lib/api 会把 detail 抛成 Error 由页面弹出

export const deleteAppointment = (id: number) =>
  call<{ ok: true }>(`/appointments/${id}`, { method: 'DELETE' })

export const deleteServiceApplication = (id: number) =>
  call<{ ok: true }>(`/service-applications/${id}`, { method: 'DELETE' })

// ---------------------------------------------------------------------------
// 首页配图

export const getHomeMedia = () => call<HomeMediaResult>('/home-media')

/** 整组替换某个位置的图，数组顺序即首页上的展示顺序。传空数组是清空。 */
export const saveHomeMedia = (slot: HomeSlot, keys: string[]) =>
  call<{ ok: true; slot: HomeSlot; count: number }>(`/home-media/${slot}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keys }),
  })

/**
 * 传一张图，拿回 COS 对象键。
 *
 * 请求体是裸的文件字节，不是 FormData：服务端解析 multipart 要多装一个依赖，
 * 而这里一次只传一张图，裸 body 就够（服务端按文件头判类型，不看文件名）。
 * 只上传、不落库——运营点了「保存」才会写进配置。
 */
export const uploadHomeImage = (file: File) =>
  call<HomeMediaUploadResult>('/home-media/upload', {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream' },
    body: file,
  })

// ---------------------------------------------------------------------------
// 安玺·集

const json = (body: unknown): RequestInit => ({
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

// 分类
//
// 没有删除接口，只有停用：商品的分类是必填的，真删掉分类就得回答「这些商品归谁」。
// 停用则语义干净——分类不再出现在小程序和新建商品的下拉里，其下商品一并下架。

export const listCategories = () => call<{ ok: true; items: ShopCategory[] }>('/shop/categories')

export const createCategory = (body: { name: string; sortOrder: number }) =>
  call<{ ok: true; id: string }>('/shop/categories', { method: 'POST', ...json(body) })

export const updateCategory = (id: string, body: { name: string; sortOrder: number }) =>
  call<{ ok: true }>(`/shop/categories/${id}`, { method: 'PUT', ...json(body) })

/** 停用会连带下架该分类下的在售商品，返回值里的 takenDown 是被下架的件数 */
export const setCategoryStatus = (id: string, status: CategoryStatus) =>
  call<{ ok: true; takenDown: number }>(`/shop/categories/${id}/status`, {
    method: 'PUT',
    ...json({ status }),
  })

/**
 * 把该分类下已下架的商品批量上架。
 *
 * 停用分类是不可逆的批量下架，这是它的显式反向操作。没有封面图的商品会被跳过
 * （上架规则要求至少一张图），跳过几件由 skippedWithoutImage 报出来。
 */
export const activateCategoryProducts = (id: string) =>
  call<{ ok: true; activated: number; skippedWithoutImage: number }>(
    `/shop/categories/${id}/activate-products`,
    { method: 'POST' },
  )

// 商品

export const listProducts = (params: ShopProductQuery & PageParams) =>
  get<PagedResult<ShopProductRow>>('/shop/products', params)

export const getProduct = (id: string) =>
  call<{ ok: true; item: ShopProductDetail }>(`/shop/products/${id}`)

export const createProduct = (body: ShopProductInput) =>
  call<{ ok: true; id: string }>('/shop/products', { method: 'POST', ...json(body) })

export const updateProduct = (id: string, body: ShopProductInput) =>
  call<{ ok: true }>(`/shop/products/${id}`, { method: 'PUT', ...json(body) })

/** 上架 / 下架。这是「商品能否被购买」的唯一开关 */
export const setProductStatus = (id: string, status: ProductStatus) =>
  call<{ ok: true }>(`/shop/products/${id}/status`, { method: 'PUT', ...json({ status }) })

/** 硬删，没有软删除。阶段二起被订单引用过的商品会被服务端以 409 挡下来 */
export const deleteProduct = (id: string) =>
  call<{ ok: true }>(`/shop/products/${id}`, { method: 'DELETE' })

/** 传一张商品图，拿回 COS 对象键。只上传、不落库——保存商品时才写进去 */
export const uploadProductImage = (file: File) =>
  call<HomeMediaUploadResult>('/shop/products/upload', {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream' },
    body: file,
  })

/* ---------------------------------------------------------------- 订单 */

/** 订单列表。关键词搜单号或收件人，日期筛的是下单日期（含当天） */
export const listOrders = (params: ShopOrderQuery & PageParams) =>
  get<PagedResult<ShopOrderRow>>('/shop/orders', params)

/** 详情。比列表多出地址快照、订单行、支付与退款信息 */
export const getOrder = (id: string) =>
  call<{ ok: true; order: ShopOrderDetail }>(`/shop/orders/${id}`)

/** 后台备注。只有运营看得到，用户端任何接口都不返回它。传空串即清空 */
export const setOrderRemark = (id: string, remark: string) =>
  call<{ ok: true }>(`/shop/orders/${id}/remark`, { method: 'PUT', ...json({ remark }) })

/**
 * 发货。服务端会在落库之后把物流信息回传给微信——微信对实物交易有发货时限，
 * 超时会判发货延迟并影响交易权限。
 *
 * 回传失败**不会**让发货失败（货可能已经交给快递了），而是在 warning 里带一句话。
 * 拿到非空 warning 一定要显示给运营：那意味着这件事还没做完。
 */
export const shipOrder = (id: string, body: ShopOrderShipInput) =>
  call<{ ok: true; warning: string }>(`/shop/orders/${id}/ship`, {
    method: 'POST',
    ...json(body),
  })

/** 改运单号。服务端会重新回传微信——不重传的话那边存的还是旧单号，用户查不到物流 */
export const updateTracking = (id: string, body: ShopOrderTrackingInput) =>
  call<{ ok: true; warning: string }>(`/shop/orders/${id}/ship`, {
    method: 'PUT',
    ...json(body),
  })

/**
 * 整单退款。**仅超管**，服务端也挂了 current_super，前端隐藏按钮只是不去误导人。
 * 金额取订单的 total_cents，请求里没有金额字段——只做整退。
 */
export const refundOrder = (id: string, reason: string) =>
  call<{ ok: true }>(`/shop/orders/${id}/refund`, { method: 'POST', ...json({ reason }) })

/** 常用快递公司编码。内置一份，不在列表里的允许手填 */
export const listExpressCompanies = () =>
  call<{ ok: true; items: ExpressCompany[] }>('/shop/express-companies')

/* ------------------------------------------------------------ 支付异常台账 */

/**
 * 「微信说钱收了，但我们没法把它落到某笔订单上」的记录。
 *
 * 这三种情况服务端最终都要向微信返回 SUCCESS（重投一百次结果一样），
 * 所以它们不会有任何重试或告警——**这个列表是唯一能看见它们的地方**。
 *
 * resolved 默认 open。别把默认值改成 all：已处理的会越积越多，
 * 默认全看等于没有默认，而这一页的意义正是「有没有需要人去处理的事」。
 */
export const listPaymentAnomalies = (params: PaymentAnomalyQuery & PageParams) =>
  get<PagedResult<PaymentAnomaly>>('/shop/payment-anomalies', params)

/**
 * 标记为已处理。**不改任何订单状态**——只是把「人已经看过并处理了」记下来。
 * 真要补单或退款各有各的入口，在这里顺手做等于开一个绕过状态机的后门。
 */
export const resolvePaymentAnomaly = (id: string, note: string) =>
  call<{ ok: true }>(`/shop/payment-anomalies/${id}/resolve`, {
    method: 'POST',
    ...json({ note }),
  })
