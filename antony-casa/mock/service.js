// 五个服务的文案与申请表单定义。一期不接后端配置，先写死在这里。
//
// 表单是数据驱动的：apply 页按 fields 数组渲染，加字段改这里就行，不用动页面。
// field.type 支持：
//   text     单行输入
//   phone    单行输入，数字键盘，11 位校验
//   number   单行输入，数字键盘，可配 unit 显示单位
//   select   底部弹出选择器，配 options
//   region   省市区选择器
//   textarea 多行输入
// uploads 数组是图片上传组，每组独立一个九宫格。
//
// ⚠️ 需求原文里，后三个服务（买手 / 售后 / 流转）没有列联系方式字段。
//    没有联系方式运营没法跟进，所以这三个也补了「客户名称 + 联系方式」，
//    并且和前两个一样从「我的信息」自动带入。不需要的话删掉对应两行即可。
//
// 📷 图存在 COS 上不进包，见 utils/config.js 的 STATIC_BASE。文件名与服务 id 同名，
//    换图就是替换 assets/service/<id>.jpg 后跑一次 server/scripts/upload_static.py，
//    小程序不用发版。规格见 assets/service/README.md。
//    留空则该卡自动退回纯文字的深色卡头，不会出裂图。

const { STATIC_BASE } = require('../utils/config.js')

const CONTACT_FIELDS = [
  { id: 'name', label: '客户名称', type: 'text', required: true, placeholder: '怎么称呼您', maxlength: 40 },
  { id: 'phone', label: '联系方式', type: 'phone', required: true, placeholder: '11 位手机号' }
]

const PROJECT_TYPES = ['住宅项目', '商业项目', '办公项目', '其他项目']

const services = [
  {
    id: 'design',
    ordinal: '01',
    image: `${STATIC_BASE}/service/design.jpg`,
    name: '全案设计服务',
    tagline: '为您的空间注入新的想象',
    intro:
      '如果您喜得新居但对装修毫无头绪；如果您怕踩雷设计师或装修被坑；如果您拥有商业空间不知如何规划，没关系，安东尼之家团队为您提供专业的全案设计服务。我们将以安东尼本身的美学思想为您提供全新的设计视野，与全球近百个知名设计品牌深度合作，结合项目实际情况，以涵盖方案设计、现场勘查、施工管理、软装艺术指导等一体化的设计服务流程，将安东尼的设计理念始终如一地贯彻到最终的设计作品中。',
    note: '',
    fields: [
      ...CONTACT_FIELDS,
      { id: 'projectType', label: '项目类别', type: 'select', options: PROJECT_TYPES },
      { id: 'address', label: '项目地址', type: 'text', placeholder: '省市区 + 小区或楼盘', maxlength: 120 },
      { id: 'area', label: '建筑面积', type: 'number', unit: '平米' },
      { id: 'budgetHard', label: '硬装预算', type: 'number', unit: '万' },
      { id: 'budgetSoft', label: '软装预算', type: 'number', unit: '万' },
      { id: 'style', label: '意向风格偏好', type: 'text', placeholder: '如 现代极简、意式轻奢', maxlength: 60 },
      { id: 'note', label: '备注说明', type: 'textarea', placeholder: '还有什么想让我们知道的', maxlength: 500 }
    ],
    uploads: [
      { id: 'style', label: '意向风格参考图片' },
      { id: 'floorplan', label: '项目平面图' }
    ]
  },

  {
    id: 'hardfit',
    ordinal: '02',
    image: `${STATIC_BASE}/service/hardfit.jpg`,
    name: '硬装施工服务',
    tagline: '为您提供装修落地服务',
    intro:
      '如果您对原始户型束手无策，担心隐蔽工程留下隐患；如果您怕硬装预算超支、材料选型踩坑，没关系，安东尼之家团队为您提供专业的硬装服务。我们将以安东尼严谨的结构美学与功能主义为基石，为您重塑空间骨架——从精准的墙体拆改、水电定位，到吊顶造型、灯光布线、瓷砖拼花与木作收口，每一处细节都经过反复推敲。依托与国内外一线建材品牌及定制工厂的深度合作，结合现场勘查、施工图深化、预算严控、全周期现场交底与验收管理，我们将安东尼「形式服从功能」的设计信条，从毛坯到硬装竣工，毫厘不差地镌刻在每一面墙、每一条动线之中，让您收获一个坚固、舒适、经得起时间检验的品质底胚。',
    note: '',
    fields: [
      ...CONTACT_FIELDS,
      { id: 'projectType', label: '项目类别', type: 'select', options: PROJECT_TYPES },
      { id: 'address', label: '项目地址', type: 'text', placeholder: '省市区 + 小区或楼盘', maxlength: 120 },
      { id: 'area', label: '建筑面积', type: 'number', unit: '平米' },
      { id: 'budgetHard', label: '硬装预算', type: 'number', unit: '万' },
      { id: 'brands', label: '意向建材品牌', type: 'text', placeholder: '有指定品牌可以写在这里', maxlength: 120 },
      { id: 'style', label: '意向风格偏好', type: 'text', placeholder: '如 现代极简、意式轻奢', maxlength: 60 },
      { id: 'note', label: '备注说明', type: 'textarea', placeholder: '还有什么想让我们知道的', maxlength: 500 }
    ],
    uploads: [
      { id: 'style', label: '意向风格参考图片' },
      { id: 'floorplan', label: '项目平面图' }
    ]
  },

  {
    id: 'buyer',
    ordinal: '03',
    image: `${STATIC_BASE}/service/buyer.jpg`,
    name: '商品买手服务',
    tagline: '满足您的个性化需求',
    intro:
      '如果您在家居、电器、生活用品或艺术品装饰等选购中举棋不定，担心尺寸不合、风格跑偏或花费冤枉钱；如果您渴望用独特的器物和艺术品提升空间气质，却缺乏鉴别与采购的专业支撑，安东尼之家团队为您提供专业的商品买手服务。我们以安东尼的美学眼光为基准，从全球独立设计品牌、百年手工工坊到新锐艺术家原作，严选涵盖家居、器物、织物、灯具、艺术版画等全品类好物。每件选品均经审美与实用性双重验证，确保与您的空间气质相融，并在日常使用中散发持久温度。让安东尼「好物即良伴」的理念，帮您将选购化为一次次有品位的珍藏。',
    note: '',
    fields: [
      ...CONTACT_FIELDS,
      { id: 'itemName', label: '商品名称', type: 'text', placeholder: '想找什么', maxlength: 60 },
      { id: 'itemBrand', label: '商品品牌', type: 'text', placeholder: '有指定品牌可以写在这里', maxlength: 60 },
      { id: 'secondHand', label: '是否接受二手/中古', type: 'select', options: ['接受', '不接受', '视品相而定'] },
      { id: 'style', label: '意向风格', type: 'text', placeholder: '如 中古、包豪斯、侘寂', maxlength: 60 },
      { id: 'quantity', label: '数量', type: 'number', unit: '件' },
      { id: 'budget', label: '购买预算', type: 'number', unit: '元' },
      { id: 'deadline', label: '可接受的最长寻购期', type: 'select', options: ['1 个月内', '3 个月内', '半年内', '一年内', '不限'] },
      { id: 'note', label: '其他备注说明', type: 'textarea', placeholder: '尺寸、颜色、使用场景等', maxlength: 500 }
    ],
    uploads: [{ id: 'item', label: '意向商品图片' }]
  },

  {
    id: 'aftersale',
    ordinal: '04',
    image: `${STATIC_BASE}/service/aftersale.jpg`,
    name: '商品售后服务',
    tagline: '始于精挑细选，终于无忧售后',
    intro:
      '如果您在为心爱的家具、艺术品等收货后的售后问题犯难；如果担心运输途中的细微磕碰得不到妥善解决；如果对日常清洁保养或使用细节存有疑问，没关系，安东尼之家团队为您提供贴心的商品售后服务。服务涵盖专业安装指导、精细修复建议、定期养护提醒，以及专属客服一对一答疑。收到货物后，若发现运输破损，我们将启动先行赔付机制，免去您的繁琐举证流程。我们用长久陪伴的态度，确保每一件好物在您家中安然落地、历久弥新。',
    note: '不提供无理由退换货服务',
    fields: [
      ...CONTACT_FIELDS,
      { id: 'itemName', label: '商品名称', type: 'text', placeholder: '哪一件商品', maxlength: 60 },
      { id: 'itemBrand', label: '商品品牌', type: 'text', maxlength: 60 },
      { id: 'buyYear', label: '商品购买年份', type: 'select', options: [] }, // 年份在 apply.js 里按当前年生成
      { id: 'demand', label: '售后需求', type: 'select', options: ['安装指导', '运输破损', '维修修复', '清洁保养', '使用咨询', '其他'] },
      { id: 'quantity', label: '数量', type: 'number', unit: '件' },
      { id: 'city', label: '商品所在城市', type: 'region' },
      { id: 'note', label: '其他备注说明', type: 'textarea', placeholder: '请描述具体情况', maxlength: 500 }
    ],
    uploads: [
      { id: 'item', label: '商品图片' },
      { id: 'damage', label: '商品磨损处细节图片' }
    ]
  },

  {
    id: 'resale',
    ordinal: '05',
    image: `${STATIC_BASE}/service/resale.jpg`,
    name: '商品流转服务',
    tagline: '让好物续遇知音',
    intro:
      '如果您家中换新后闲置的高品质好物无处安放；如果您看中了某件心仪好物却因原价过高而犹豫不决；如果您希望为空间来一次「断舍离」，让沉睡的藏品流转到真正懂它的人手中，安东尼之家团队为您提供省心的商品流转服务。我们搭建了一个仅对安东尼客户开放的好物循环平台，涵盖家具、生活器物、艺术装饰等品类。我们秉持「物尽其用，美美与共」的理念，让好物在流转中延续价值，也让您以更轻盈的方式更新生活空间。',
    note: '仅限购买自安东尼之家的产品',
    fields: [
      ...CONTACT_FIELDS,
      { id: 'itemName', label: '商品名称', type: 'text', placeholder: '想出手哪一件', maxlength: 60 },
      { id: 'buyYear', label: '商品购买年份', type: 'select', options: [] },
      { id: 'condition', label: '商品新旧程度', type: 'select', options: ['全新未拆封', '九成新', '八成新', '七成新', '六成新及以下'] },
      { id: 'quantity', label: '数量', type: 'number', unit: '件' },
      { id: 'city', label: '商品所在城市', type: 'region' },
      // 需求原文写的是「期望售后到手价」，流转场景下应为卖出后到手的价，按此命名
      { id: 'expectPrice', label: '期望到手价', type: 'number', unit: '元' },
      { id: 'note', label: '其他备注说明', type: 'textarea', placeholder: '购买渠道、使用情况等', maxlength: 500 }
    ],
    uploads: [
      { id: 'item', label: '商品图片' },
      { id: 'damage', label: '商品磨损处细节图片' },
      { id: 'receipt', label: '商品购物凭证' }
    ]
  }
]

function findService(id) {
  return services.find(s => s.id === id) || null
}

module.exports = { services, findService }
