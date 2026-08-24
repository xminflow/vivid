/**
 * 安东尼之家的选项字典。后台把库里存的 id / 枚举值翻成中文全靠这里。
 *
 * ⚠️ 同步要求：这些值必须与下面四处**逐字一致**，改一处就要改到位，
 *    否则后台会把正常数据显示成原始 id：
 *      1. server/schema.sql 的 CHECK 约束
 *      2. server/app/models.py 的 VISITOR_TYPES / PURPOSES / SERVICE_IDS
 *      3. antony-casa/mock/*.js 与 pages/booking/booking.js 的同名常量（小程序）
 *      4. antony-web/src/features/booking/content.ts 的同名常量（官网）
 *
 *    表单字段（FIELD_LABELS / UPLOAD_LABELS）只存在于小程序的 mock/service.js：
 *    一期表单写死在小程序里，服务端只当 jsonb 原样收发，不认识字段含义。
 *    所以这份字典是小程序表单定义的镜像，加字段时要一起加。
 *    没登记的字段不会被隐藏，页面直接显示原始 id，避免运营漏看客户填的内容。
 */

import type { StatusMeta } from '@/components/status-badge'

import type {
  OrderStatus,
  PaymentAnomalyKind,
  PaymentAnomalySource,
  ServiceId,
} from './types'

export const VISITOR_TYPES = ['业主', '设计师', '地产圈', '家居圈', '酒店民宿圈', '艺术圈'] as const

export const PURPOSES = [
  '展厅参观',
  '全案设计咨询',
  '装修建材订购',
  '家具软装选购',
  '商务合作',
  '其他',
] as const

export const SERVICES: { value: ServiceId; label: string }[] = [
  { value: 'design', label: '全案设计服务' },
  { value: 'hardfit', label: '硬装施工服务' },
  { value: 'buyer', label: '商品买手服务' },
  { value: 'aftersale', label: '商品售后服务' },
  { value: 'resale', label: '商品流转服务' },
]

/**
 * 申请表单的字段名。五个服务共用一张表，字段 id 也跨服务复用（itemName 在
 * 买手 / 售后 / 流转里都出现），所以是一张扁平字典，不按服务分组。
 * unit 与小程序表单里的单位一致，缺了单位数字没法读（120 是平米还是万）。
 */
export const FIELD_LABELS: Record<string, { label: string; unit?: string }> = {
  projectType: { label: '项目类别' },
  address: { label: '项目地址' },
  area: { label: '建筑面积', unit: '平米' },
  budgetHard: { label: '硬装预算', unit: '万' },
  budgetSoft: { label: '软装预算', unit: '万' },
  brands: { label: '意向建材品牌' },
  style: { label: '意向风格偏好' },
  itemName: { label: '商品名称' },
  itemBrand: { label: '商品品牌' },
  secondHand: { label: '是否接受二手/中古' },
  quantity: { label: '数量', unit: '件' },
  budget: { label: '购买预算', unit: '元' },
  deadline: { label: '可接受的最长寻购期' },
  buyYear: { label: '商品购买年份' },
  demand: { label: '售后需求' },
  city: { label: '商品所在城市' },
  condition: { label: '商品新旧程度' },
  expectPrice: { label: '期望到手价', unit: '元' },
  note: { label: '备注说明' },
}

/** 图片上传组 */
export const UPLOAD_LABELS: Record<string, string> = {
  style: '意向风格参考图片',
  floorplan: '项目平面图',
  item: '商品图片',
  damage: '商品磨损处细节图片',
  receipt: '商品购物凭证',
}

export const serviceLabel = (id: string): string =>
  SERVICES.find((s) => s.value === id)?.label ?? id

/** 未登记的字段照原样显示 id，宁可难看也不能把客户填的内容藏起来 */
export const fieldLabel = (id: string): string => FIELD_LABELS[id]?.label ?? id

export const fieldValue = (id: string, value: string): string => {
  const unit = FIELD_LABELS[id]?.unit
  return unit ? `${value} ${unit}` : value
}

export const uploadLabel = (id: string): string => UPLOAD_LABELS[id] ?? id

/* ---------------------------------------------------------------- 订单 */

/**
 * 订单状态。值必须与 server/app/models.py 的 ORDER_STATUSES、
 * schema.sql 的 shop_orders.status CHECK、以及小程序
 * pages/orders/orders.js 的 STATUS_TEXT 逐字一致——改一处要改四处。
 * 不一致的后果是后台筛选永远筛出空列表。
 */
export const ORDER_STATUSES = [
  { value: 'pending_pay', label: '待付款' },
  { value: 'pending_ship', label: '待发货' },
  { value: 'pending_receive', label: '待收货' },
  { value: 'completed', label: '已完成' },
  { value: 'closed', label: '已关闭' },
  { value: 'refunded', label: '已退款' },
] as const

/**
 * 徽标配色只表达「要不要处理」：待付款是等客户、待发货是**等我们**，
 * 所以只有待发货给显眼色。已完成收敛，关闭和退款压灰。
 */
export const ORDER_STATUS_META: Record<OrderStatus, StatusMeta> = {
  pending_pay: { label: '待付款', tone: 'muted' },
  pending_ship: { label: '待发货', tone: 'pending' },
  pending_receive: { label: '待收货', tone: 'active' },
  completed: { label: '已完成', tone: 'done' },
  closed: { label: '已关闭', tone: 'muted' },
  refunded: { label: '已退款', tone: 'muted' },
}

export const ORDER_SOURCES = [
  { value: 'cart', label: '购物车' },
  { value: 'direct', label: '立即购买' },
] as const

/** 三档发货方式。卖家具走专线或自送时没有运单号，后两档是刚需 */
export const SHIPPING_TYPES = [
  { value: 'express', label: '快递发货' },
  { value: 'local', label: '同城配送' },
  { value: 'none', label: '无需物流' },
] as const

/* ---------------------------------------------------------------- 支付异常 */

/**
 * 三种异常的名字与「这到底是什么事」。
 *
 * 提示语写得长一点是有意的：运营看到这一行时，手里只有一个订单号和一笔已经到账
 * 的钱，需要知道下一步该去哪儿查。写「金额不符」四个字等于没说。
 */
export const ANOMALY_KINDS = [
  { value: 'amount_mismatch', label: '金额不符' },
  { value: 'order_not_found', label: '订单不存在' },
  { value: 'missing_transaction_id', label: '缺支付单号' },
] as const

export const ANOMALY_KIND_LABEL: Record<PaymentAnomalyKind, string> = {
  amount_mismatch: '金额不符',
  order_not_found: '订单不存在',
  missing_transaction_id: '缺支付单号',
}

export const ANOMALY_KIND_HINT: Record<PaymentAnomalyKind, string> = {
  amount_mismatch:
    '微信收到的金额与订单金额不一致，订单没有被置为已支付。要么建单时算错了，要么金额在中间被改过。' +
    '拿微信支付单号去商户平台核对实际到账金额，再决定是补单还是原路退回。',
  order_not_found:
    '收到一笔支付成功，但这个商户订单号在我们库里不存在——钱收了却对不上任何订单。' +
    '先确认这个单号是不是别的环境（开发/生产共用同一个商户号）发出去的，' +
    '再去商户平台看这笔钱的去向。',
  missing_transaction_id:
    '微信说支付成功却没给支付单号。没有它就没法保证不重复入账，所以这笔支付被挂起、订单仍是待付款。' +
    '去商户平台按订单号查到真实的支付单号，再人工处理。',
}

export const ANOMALY_SOURCE_LABEL: Record<PaymentAnomalySource, string> = {
  notify: '支付回调',
  sync_pay: '主动查单',
  sweep: '超时扫描',
}

/**
 * 默认只看待处理的。已处理的会越积越多，默认全看等于没有默认——
 * 而这个列表的意义正是「有没有需要人去处理的事」。
 */
export const ANOMALY_RESOLVED_OPTIONS = [
  { value: 'open', label: '待处理' },
  { value: 'done', label: '已处理' },
  { value: 'all', label: '全部' },
] as const

/** 订单关闭原因。never_submitted 是超时扫描发现「微信侧根本没有这笔单」时记的 */
export const CLOSE_REASON_LABEL: Record<string, string> = {
  timeout: '超时未支付',
  user_cancel: '用户取消',
  never_submitted: '未成功提交到微信',
}
