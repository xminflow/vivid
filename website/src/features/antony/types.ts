/** 安东尼之家后台接口的返回类型，与 server/app/admin.py 一一对应。 */

export interface Appointment {
  id: number
  name: string
  phone: string
  visitorType: string
  /** YYYY-MM-DD */
  visitDate: string
  partySize: number
  purpose: string
  note: string
  /** 从哪张展厅卡片点进表单的，可能没有 */
  spaceId: string | null
  /**
   * 提交人。未登录也能提交，所以可能为空。
   * 服务端出的是字符串——雪花 ID 有 18 位，超过 Number.MAX_SAFE_INTEGER，当数字用会丢精度
   */
  userId: string | null
  createdAt: string
}

// 筛选条件写成 type 而不是 interface：type 才有隐式索引签名，
// 才能作为 Record<string, string> 传给通用的取数函数
export type AppointmentQuery = {
  keyword: string
  visitorType: string
  purpose: string
  visitDateFrom: string
  visitDateTo: string
}

/** 服务申请里的一张图。url 为 null 表示服务端没配 COS，签不出浏览地址 */
export interface ApplicationImage {
  key: string
  url: string | null
}

export interface ServiceApplication {
  id: number
  serviceId: ServiceId
  name: string
  phone: string
  /** 各服务自己的表单字段，键是字段 id，见 antony-casa/mock/service.js */
  fields: Record<string, string>
  /** 键是上传组 id */
  images: Record<string, ApplicationImage[]>
  createdAt: string
}

export type ServiceId = 'design' | 'hardfit' | 'buyer' | 'aftersale' | 'resale'

export type ServiceApplicationQuery = {
  keyword: string
  serviceId: string
  createdFrom: string
  createdTo: string
}

// ---------------------------------------------------------------------------
// 首页配图

/** 首页上的三个位置，与 server/app/models.py 的 HOME_SLOTS 逐字一致 */
export type HomeSlot = 'hero' | 'showroom' | 'activity'

/** 一张首页图。url 为 null 表示服务端没配 COS，拼不出可访问地址 */
export interface HomeMediaItem {
  /** 雪花 ID，服务端出的是字符串（18 位，超过 Number.MAX_SAFE_INTEGER） */
  id: string
  /** COS 对象键，保存时回传给服务端的就是它 */
  key: string
  url: string | null
}

export interface HomeMediaResult {
  ok: true
  slots: Record<HomeSlot, HomeMediaItem[]>
  /** 每个位置最多放几张，由服务端给，前端不写死 */
  limits: Record<HomeSlot, number>
}

export interface HomeMediaUploadResult {
  ok: true
  key: string
  url: string
}

// ---------------------------------------------------------------------------
// 安玺·集
//
// 与 server/app/shop_admin.py 一一对应。术语见仓库根目录的 CONTEXT.md：
// 「商品 / 参数 / 简介 / 详情图 / 图集 / 在售 / 分类」都是有确切含义的词，别换说法。

/** 分类的两态。与 server/app/models.py 的 CATEGORY_STATUSES 逐字一致 */
export type CategoryStatus = 'active' | 'disabled'

/**
 * 商品的两态。没有草稿：新建即 off，填完再上架。
 * 与 server/app/models.py 的 PRODUCT_STATUSES 逐字一致
 */
export type ProductStatus = 'active' | 'off'

export interface ShopCategory {
  /** 雪花 ID，服务端出的是字符串 */
  id: string
  name: string
  sortOrder: number
  status: CategoryStatus
  /** 该分类下的商品总数。停用会连带下架，点之前要让运营看见影响面 */
  productCount: number
  /** 其中在售的件数 */
  activeCount: number
  createdAt: string
}

/** 一张商品图。url 为 null 表示服务端没配 COS，拼不出可访问地址 */
export interface ShopImage {
  key: string
  url: string | null
}

/** 一条展示型参数。不影响价格与可购性 */
export interface ProductParam {
  name: string
  value: string
}

/** 列表页的商品。只带封面，整组图在详情接口里 */
export interface ShopProductRow {
  id: string
  categoryId: string
  categoryName: string
  title: string
  /** 金额一律用分。浮点算钱迟早出对账差 */
  priceCents: number
  cover: ShopImage | null
  status: ProductStatus
  sortOrder: number
  createdAt: string
}

/** 编辑页的商品，全量字段 */
export interface ShopProductDetail extends ShopProductRow {
  summary: string
  images: ShopImage[]
  detailImages: ShopImage[]
  params: ProductParam[]
  updatedAt: string
}

export type ShopProductQuery = {
  keyword: string
  categoryId: string
  status: string
}

/** 提交给服务端的商品。categoryId 是字符串——雪花 ID 当数字用会丢精度 */
export interface ShopProductInput {
  categoryId: string
  title: string
  summary: string
  priceCents: number
  images: string[]
  detailImages: string[]
  params: ProductParam[]
  sortOrder: number
}

/* ---------------------------------------------------------------- 订单 */

/** 与 server/app/models.py 的 ORDER_STATUSES、schema.sql 的 CHECK 逐字一致 */
export type OrderStatus =
  | 'pending_pay'
  | 'pending_ship'
  | 'pending_receive'
  | 'completed'
  | 'closed'
  | 'refunded'

/** 从购物车结算还是详情页「立即购买」 */
export type OrderSource = 'cart' | 'direct'

export type ShippingType = 'express' | 'local' | 'none'

/** 列表行。不含订单明细——一页 20 单，明细只在详情里取 */
export interface ShopOrderRow {
  id: string
  orderNo: string
  status: OrderStatus
  source: OrderSource
  totalCents: number
  receiver: string
  phone: string
  createdAt: string
  paidAt: string | null
  shippedAt: string | null
  shippingType: ShippingType | null
  trackingNo: string | null
  remark: string
}

/** 订单行。全部是下单那一刻的快照，不回查商品当前值 */
export interface ShopOrderItem {
  productId: string
  title: string
  priceCents: number
  cover: string | null
  quantity: number
}

export interface ShopOrderDetail {
  id: string
  orderNo: string
  status: OrderStatus
  source: OrderSource
  totalCents: number
  /** 下单人在小程序里的资料，和收件人不一定是同一个人 */
  buyer: string
  address: {
    receiver: string
    phone: string
    province: string
    city: string
    district: string
    detail: string
  }
  payment: {
    /** 微信支付订单号，对账时拿它去商户平台查 */
    transactionId: string | null
    paidAt: string | null
  }
  shipping: {
    type: ShippingType | null
    company: string | null
    trackingNo: string | null
    shippedAt: string | null
  }
  receivedAt: string | null
  closedAt: string | null
  /**
   * never_submitted：下单那一刻就没成功提交到微信，微信侧根本没有这笔单，
   * 由超时扫描本地关掉。与 timeout（用户没在时限内付）分开——排查方向不同。
   */
  closeReason: 'timeout' | 'user_cancel' | 'never_submitted' | null
  refund: {
    refundedAt: string | null
    reason: string | null
    refundId: string | null
  }
  remark: string
  createdAt: string
  items: ShopOrderItem[]
}

export type ShopOrderQuery = {
  keyword: string
  status: string
  createdFrom: string
  createdTo: string
}

/** 发货。走快递必须给物流公司编码和运单号，另两档必须不给 */
export interface ShopOrderShipInput {
  shippingType: ShippingType
  shippingCompany?: string
  trackingNo?: string
}

/** 改运单号。只有快递单才有得改 */
export interface ShopOrderTrackingInput {
  shippingCompany: string
  trackingNo: string
}

/** 微信的标准快递公司编码 */
export interface ExpressCompany {
  code: string
  name: string
}

/* ---------------------------------------------------------------- 支付异常 */

/**
 * 三种「微信说钱收了，但我们没法把它落到某笔订单上」的情况。
 * 与 server/app/payment_anomalies.py 的 AnomalyKind 逐字一致。
 */
export type PaymentAnomalyKind =
  | 'amount_mismatch'
  | 'order_not_found'
  | 'missing_transaction_id'

/** 哪条通道发现的。排查时要先知道是回调、主动查单还是超时扫描发现的 */
export type PaymentAnomalySource = 'notify' | 'sync_pay' | 'sweep'

export interface PaymentAnomaly {
  id: string
  kind: PaymentAnomalyKind
  source: PaymentAnomalySource
  orderNo: string
  /** order_not_found 时为空——那一类恰恰是「没有对应订单」 */
  orderId: string | null
  transactionId: string
  paidCents: number | null
  /** order_not_found 时为空：没有那笔订单，也就没有「应付多少」 */
  expectedCents: number | null
  resolvedAt: string | null
  resolvedBy: string
  resolveNote: string
  createdAt: string
}

export type PaymentAnomalyQuery = {
  /** open（默认）/ done / all。不是「全部」优先——已处理的越积越多，默认全看等于没有默认 */
  resolved: string
}
