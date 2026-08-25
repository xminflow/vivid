"""表单模型。选项值必须与 schema.sql 的 CHECK 约束逐字一致。"""

import re
from datetime import date, datetime, time
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

from .security import PASSWORD_MAX_LEN, PASSWORD_MIN_LEN

VISITOR_TYPES = ("业主", "设计师", "地产圈", "家居圈", "酒店民宿圈", "艺术圈")
PURPOSES = ("展厅参观", "全案设计咨询", "装修建材订购", "家具软装选购", "商务合作", "其他")
# 与小程序 mock/mine.js 的 genders 一致，多一个 '' 表示没填
GENDERS = ("", "女", "男", "不便告知")

VisitorType = Literal["业主", "设计师", "地产圈", "家居圈", "酒店民宿圈", "艺术圈"]
Purpose = Literal["展厅参观", "全案设计咨询", "装修建材订购", "家具软装选购", "商务合作", "其他"]
Gender = Literal["", "女", "男", "不便告知"]

# 生日选择器的下限，与 pages/mine/mine.js 的 BIRTHDAY_START 一致
BIRTHDAY_START = date(1930, 1, 1)

# 小程序传的是驼峰，库里是下划线，模型两边都收
Trimmed = Annotated[str, StringConstraints(strip_whitespace=True)]


class AppointmentIn(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    name: Annotated[Trimmed, Field(min_length=1, max_length=40)]
    phone: Annotated[Trimmed, Field(pattern=r"^1[3-9]\d{9}$")]
    visitor_type: VisitorType
    visit_date: date
    # 到访时刻，可以没有。库里 016 迁移之前的记录都没有这一列；而且服务端要先于
    # 小程序上线，那几天里旧版本提交的表单不带 visitTime，设成必填会把它们整片
    # 挡在门外。新版小程序一定会带。
    visit_time: time | None = None
    party_size: Annotated[int, Field(ge=1, le=50)]
    purpose: Purpose
    note: Annotated[Trimmed, Field(max_length=500)] = ""
    space_id: Trimmed | None = None

    @field_validator("visit_date")
    @classmethod
    def not_in_the_past(cls, v: date) -> date:
        # 按服务器本地日期比，展厅和用户都在国内，不做时区换算
        if v < date.today():
            raise ValueError("到访日期不能早于今天")
        return v

    @model_validator(mode="after")
    def visit_time_not_in_the_past(self) -> "AppointmentIn":
        """选了今天的话，时刻也不能是已经过去的。

        上面那道只比到「天」：今天下午三点填一个今天上午十点，它是拦不住的。
        所以只在日期正好是今天时再比一次时刻，明天以后的任何时刻都合法。
        """
        if (
            self.visit_time is not None
            and self.visit_date == date.today()
            and self.visit_time < datetime.now().time()
        ):
            raise ValueError("到访时间已经过了，请重新选择")
        return self

    @field_validator("space_id")
    @classmethod
    def blank_to_none(cls, v: str | None) -> str | None:
        return v or None


class LoginIn(BaseModel):
    """wx.login 拿到的 code，换 openid 用。昵称头像是用户点了授权才有的，可以没有。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    code: Annotated[Trimmed, Field(min_length=1)]
    nickname: Annotated[Trimmed, Field(max_length=60)] = ""
    avatar_url: Trimmed = ""


def check_upload_key(key: str) -> str:
    """只收本服务端签发过的对象键。

    客户端传来的是键不是 URL，若不校验，任何人都能把任意字符串写进库，
    之后按键签出的地址就会指向桶里别的对象。
    """
    if not key.startswith("uploads/") or ".." in key:
        raise ValueError("图片标识不合法")
    return key


def check_static_key(key: str) -> str:
    """运营素材的对象键校验，同 check_upload_key，只是前缀不同。

    首页图的地址是公开直链，键写错不会像用户上传那样只是签不出地址，而是
    直接把首页挂成一片裂图，所以前缀必须卡死在 static/ 下。
    """
    if not key.startswith("static/") or ".." in key:
        raise ValueError("图片标识不合法")
    if len(key) > 200:
        raise ValueError("图片标识过长")
    return key


class AvatarIn(BaseModel):
    """换头像。传的是 /api/upload-url 签发、客户端直传完成的 COS 对象键。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    avatar_key: Annotated[Trimmed, Field(min_length=1, max_length=200)]

    @field_validator("avatar_key")
    @classmethod
    def key_is_one_of_ours(cls, v: str) -> str:
        return check_upload_key(v)


class ProfileIn(BaseModel):
    """「我的信息」整份提交，字段都可以留空——用户填一半就退出是常态。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    member_name: Annotated[Trimmed, Field(max_length=40)] = ""
    phone: Trimmed = ""
    email: Annotated[Trimmed, Field(max_length=120)] = ""
    birthday: date | None = None
    gender: Gender = ""
    # picker mode="region" 给的是 [省, 市, 区]
    region: list[Trimmed] = Field(default_factory=list, max_length=3)

    @field_validator("phone")
    @classmethod
    def phone_is_blank_or_a_mobile_number(cls, v: str) -> str:
        if v and not re.fullmatch(r"1[3-9]\d{9}", v):
            raise ValueError("电话格式不正确")
        return v

    @field_validator("email")
    @classmethod
    def email_is_blank_or_an_address(cls, v: str) -> str:
        if v and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("邮箱格式不正确")
        return v

    @field_validator("birthday")
    @classmethod
    def birthday_is_in_range(cls, v: date | None) -> date | None:
        if v is None:
            return v
        if v < BIRTHDAY_START or v > date.today():
            raise ValueError("生日不在可选范围内")
        return v

    def region_parts(self) -> tuple[str, str, str]:
        """补齐成 (省, 市, 区)，少选的层级留空串。"""
        parts = [*self.region, "", "", ""]
        return parts[0], parts[1], parts[2]


class PhoneCodeIn(BaseModel):
    """getPhoneNumber 回调给的一次性 code，换手机号明文用。

    与 LoginIn.code 不是同一个东西：那个来自 wx.login、换的是 openid，
    这个来自 <button open-type="getPhoneNumber"> 的回调、换的是手机号。
    两者都是一次性的，用过即废。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    code: Annotated[Trimmed, Field(min_length=1, max_length=200)]


# ---------------------------------------------------------------------------
# 服务申请

SERVICE_IDS = ("design", "hardfit", "buyer", "aftersale", "resale")
ServiceId = Literal["design", "hardfit", "buyer", "aftersale", "resale"]

# 单个申请里所有图片加起来的上限。防止有人拿这个接口当图床
MAX_IMAGES = 30
MAX_FIELD_LEN = 1000


class UploadUrlIn(BaseModel):
    """换一个 COS 直传地址。scene 只用来分目录，ext 是白名单校验过的后缀。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    scene: Annotated[Trimmed, Field(min_length=1, max_length=40)] = "misc"
    ext: Annotated[Trimmed, Field(min_length=1, max_length=8)]


class ServiceApplicationIn(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    service_id: ServiceId
    name: Annotated[Trimmed, Field(min_length=1, max_length=40)]
    phone: Annotated[Trimmed, Field(pattern=r"^1[3-9]\d{9}$")]
    fields: dict[str, str] = {}
    images: dict[str, list[str]] = {}

    @field_validator("fields")
    @classmethod
    def fields_not_too_long(cls, v: dict[str, str]) -> dict[str, str]:
        for key, value in v.items():
            if len(value) > MAX_FIELD_LEN:
                raise ValueError(f"{key} 填写内容过长")
        return v

    @field_validator("images")
    @classmethod
    def keys_look_like_ours(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        total = sum(len(keys) for keys in v.values())
        if total > MAX_IMAGES:
            raise ValueError(f"图片最多 {MAX_IMAGES} 张")

        for keys in v.values():
            for key in keys:
                check_upload_key(key)
        return v


# ---------------------------------------------------------------------------
# 首页配图

# 与 schema.sql 的 home_media.slot CHECK 逐字一致
HOME_SLOTS = ("hero", "showroom", "activity", "share")
HomeSlot = Literal["hero", "showroom", "activity", "share"]

# 每个位置最多放几张。数字按首页实际的版面定，不留「余量」——
# 上限放宽只会让运营多传的图悄悄不显示，还不如在后台就拦住
#   hero     首屏画廊五张
#   showroom 展厅参观动线六张
#   activity 首页是整张海报铺开、不是轮播，只能一张。库上也有唯一索引兜着
#   share    转发卡片配图（小程序封面），一张。它不在首页上，只是共用同一张表
#            和同一套读写逻辑；没配时小程序退回 hero 首图，见 app/home.py
HOME_SLOT_MAX = {"hero": 5, "showroom": 6, "activity": 1, "share": 1}


class HomeMediaIn(BaseModel):
    """整组替换某个位置的图。

    传的是**整组**而不是单张增删：顺序由数组下标决定，一次提交就是一个完整状态，
    后台两个人同时改也不会出现「一半新一半旧」的中间态。
    传空数组表示清空该位置，小程序会回退到包内的默认图（见 app/home.py）。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    keys: list[Trimmed] = Field(default_factory=list)

    @field_validator("keys")
    @classmethod
    def keys_look_like_ours(cls, v: list[str]) -> list[str]:
        for key in v:
            check_static_key(key)
        # 同一组里不能有重复：小程序的 wx:for 用图片地址做 wx:key，
        # 重复的 key 会让列表复用节点时错位
        if len(set(v)) != len(v):
            raise ValueError("同一组里有重复的图片")
        return v


# ---------------------------------------------------------------------------
# 管理端账号
#
# 注意这一节的模型服务的是**后台管理员**，与上面 users 表那套（小程序用户）
# 完全是两回事，不要混用。

# 与 schema.sql 的 admin_users.username CHECK 逐字一致
ADMIN_USERNAME_PATTERN = r"^[a-zA-Z0-9_.-]{3,32}$"

AdminUsername = Annotated[Trimmed, Field(pattern=ADMIN_USERNAME_PATTERN)]
# 长度上下限直接引用 app/security.py 的常量，不再抄一份字面量：
# 那里解释了为什么不强制字符组合，两处各写一份数字迟早会改一处漏一处
AdminPassword = Annotated[str, Field(min_length=PASSWORD_MIN_LEN, max_length=PASSWORD_MAX_LEN)]


class AdminLoginIn(BaseModel):
    """登录。

    这里**不**按 ADMIN_USERNAME_PATTERN 卡用户名：格式不对的用户名本来就登不上，
    提前回一个「格式不正确」等于告诉爆破的人这批候选不用试。一律走到密码比对，
    回同一句「用户名或密码不正确」。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    username: Annotated[Trimmed, Field(min_length=1, max_length=64)]
    password: Annotated[str, Field(min_length=1, max_length=200)]


class AdminPasswordChangeIn(BaseModel):
    """改自己的密码。旧密码必填，防的是有人借着没锁屏的电脑改密码顶掉本人。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    old_password: Annotated[str, Field(min_length=1, max_length=200)]
    new_password: AdminPassword


class AdminAccountIn(BaseModel):
    """超管建号。没有注册入口，账号只能从这里来。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    username: AdminUsername
    display_name: Annotated[Trimmed, Field(max_length=40)] = ""
    password: AdminPassword


class AdminPasswordResetIn(BaseModel):
    """超管重置别人的密码。不要旧密码——超管本来就不知道对方的密码。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    password: AdminPassword


class AdminStatusIn(BaseModel):
    """启用 / 停用。取值与 schema.sql 的 admin_users.status CHECK 逐字一致。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    status: Literal["active", "disabled"]


# ---------------------------------------------------------------------------
# 安玺·集 商品目录
#
# 设计见 docs/superpowers/specs/2026-08-10-anxi-ji-design.md。
# 取值与 schema.sql 的 shop_categories / shop_products CHECK 逐字一致。

CATEGORY_STATUSES = ("active", "disabled")
CategoryStatus = Literal["active", "disabled"]

# 商品只有「在售 / 下架」两态，没有草稿：新建即 off，运营填完再上架。
# off 而不是 disabled，是为了跟管理员账号那套状态词区分开——两者不是一回事
PRODUCT_STATUSES = ("active", "off")
ProductStatus = Literal["active", "off"]

# 各项数量上限。schema.sql 的 CHECK 里也写了一份：
# 库上那份是防「绕过接口直接写库」，这份是为了能回一句人话而不是 500。
# 改的时候两处一起改
MAX_PRODUCT_IMAGES = 10
MAX_PRODUCT_DETAIL_IMAGES = 20
MAX_PRODUCT_PARAMS = 20

# 单价上限一百万元。不是业务上限，是**手滑上限**——多打两个零就是一百倍的价，
# 而安玺·集接的是真实微信支付，这种错会直接变成一笔真的收款
MAX_PRICE_CENTS = 100_000_000

# 雪花 ID 出接口时是字符串（超出 JS 安全整数范围），进接口时自然也是字符串。
# 这里只卡形状，是否真的存在由外键和查询去判
SnowflakeRef = Annotated[Trimmed, Field(pattern=r"^\d{1,19}$")]


class ShopCategoryIn(BaseModel):
    """新建 / 编辑分类。

    没有删除接口：分类只能停用。原因见 schema.sql 里 shop_categories 的注释。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    name: Annotated[Trimmed, Field(min_length=1, max_length=20)]
    sort_order: int = 0


class ShopCategoryStatusIn(BaseModel):
    """停用 / 启用分类。停用会连带下架该分类下的全部在售商品。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    status: CategoryStatus


class ProductParam(BaseModel):
    """一条展示型参数，例如「材质 / 实木」。

    自由名值对而不是预设字段集：家居品类差异太大，固定字段会让灯具留着一片
    「坐深」的空格。代价是运营录入不统一，靠录入规范约束，不靠模型。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    name: Annotated[Trimmed, Field(min_length=1, max_length=20)]
    value: Annotated[Trimmed, Field(min_length=1, max_length=100)]


class ShopProductIn(BaseModel):
    """新建 / 编辑商品。整份提交，不做字段级 PATCH。

    images 允许为空：新建的商品一律是「下架」态，运营常常先把文字存下来再回头传图。
    「上架必须有封面」是状态迁移时才校验的事，不在这里卡（见 app/shop_admin.py）。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    category_id: SnowflakeRef
    title: Annotated[Trimmed, Field(min_length=1, max_length=60)]
    summary: Annotated[Trimmed, Field(max_length=500)] = ""
    price_cents: Annotated[int, Field(gt=0, le=MAX_PRICE_CENTS)]
    images: list[Trimmed] = Field(default_factory=list)
    detail_images: list[Trimmed] = Field(default_factory=list)
    params: list[ProductParam] = Field(default_factory=list)
    sort_order: int = 0

    @field_validator("images")
    @classmethod
    def images_look_like_ours(cls, v: list[str]) -> list[str]:
        return _check_image_list(v, MAX_PRODUCT_IMAGES, "商品图")

    @field_validator("detail_images")
    @classmethod
    def detail_images_look_like_ours(cls, v: list[str]) -> list[str]:
        return _check_image_list(v, MAX_PRODUCT_DETAIL_IMAGES, "详情图")

    @field_validator("params")
    @classmethod
    def params_are_within_limits(cls, v: list[ProductParam]) -> list[ProductParam]:
        if len(v) > MAX_PRODUCT_PARAMS:
            raise ValueError(f"参数最多 {MAX_PRODUCT_PARAMS} 条")
        names = [item.name for item in v]
        # 同名参数在详情页会并排出现两行「材质」，看起来像数据错了
        if len(set(names)) != len(names):
            raise ValueError("参数名不能重复")
        return v


def _check_image_list(keys: list[str], limit: int, label: str) -> list[str]:
    """商品图与详情图共用的校验：数量、前缀、重复。

    重复要拦是因为小程序的 wx:for 拿图片地址当 wx:key，同一组里出现两个相同的键，
    列表复用节点时会错位。这一点与首页配图（HomeMediaIn）同理。
    """
    if len(keys) > limit:
        raise ValueError(f"{label}最多 {limit} 张")
    for key in keys:
        check_static_key(key)
    if len(set(keys)) != len(keys):
        raise ValueError(f"{label}里有重复的图片")
    return keys


class ShopProductStatusIn(BaseModel):
    """上架 / 下架。取值与 schema.sql 的 shop_products.status CHECK 逐字一致。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    status: ProductStatus


# ---------------------------------------------------------------------------
# 购物车

# 一个人车里最多几行。不是技术上限，是防「拿购物车当收藏夹」——
# 那样每次打开都要回查几百件商品的当前价格和在售状态
MAX_CART_ITEMS = 50
# 单行最多几件。与 schema.sql 的 shop_cart_items_quantity_range 逐字一致
MAX_CART_QUANTITY = 99


class CartItemIn(BaseModel):
    """加购。同一件商品重复加是**累加数量**，不是多出一行（库上有唯一约束兜着）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    product_id: SnowflakeRef
    quantity: Annotated[int, Field(ge=1, le=MAX_CART_QUANTITY)] = 1


class CartQuantityIn(BaseModel):
    """改数量。改成 0 不是删除——删除走 DELETE，两个动作分开，
    免得前端少传一位就把用户的车清了。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    quantity: Annotated[int, Field(ge=1, le=MAX_CART_QUANTITY)]


# ---------------------------------------------------------------------------
# 收货地址与订单

# 一个人最多存几条地址。防的是「把地址簿当草稿本」，不是技术上限
MAX_ADDRESSES = 20

# 订单状态。取值与 schema.sql 的 shop_orders.status CHECK 逐字一致。
#
#   pending_pay ──支付成功──> pending_ship ──发货──> pending_receive ──收货──> completed
#        ├──超时/用户取消──> closed
#        └──（已支付后由超管退款）──> refunded
ORDER_STATUSES = (
    "pending_pay",
    "pending_ship",
    "pending_receive",
    "completed",
    "closed",
    "refunded",
)
OrderStatus = Literal[
    "pending_pay", "pending_ship", "pending_receive", "completed", "closed", "refunded"
]

# 一单最多几种商品。与购物车行数上限一致：结算页要逐行回查商品的当前价格和在售状态，
# 行数没有上限的话，一次下单能拖出几百条查询
MAX_ORDER_ITEMS = MAX_CART_ITEMS

# 单笔订单金额上限。同 MAX_PRICE_CENTS，是**手滑上限**不是业务上限——
# 接的是真实微信支付，多一个零就是一笔真的巨额收款
MAX_ORDER_TOTAL_CENTS = MAX_PRICE_CENTS


class ShopAddressIn(BaseModel):
    """新增 / 编辑收货地址。整份提交，不做字段级 PATCH。

    省市区是三个独立字段而不是一个拼好的字符串：小程序用 picker 的 region 模式选，
    出来本来就是三段；将来要按省份统计或限制配送范围，拆开的才用得上。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    receiver: Annotated[Trimmed, Field(min_length=1, max_length=20)]
    # 与 AppointmentIn.phone、schema.sql 的三处 CHECK 同一条正则
    phone: Annotated[Trimmed, Field(pattern=r"^1[3-9]\d{9}$")]
    province: Annotated[Trimmed, Field(min_length=1, max_length=100)]
    city: Annotated[Trimmed, Field(min_length=1, max_length=100)]
    district: Annotated[Trimmed, Field(min_length=1, max_length=100)]
    detail: Annotated[Trimmed, Field(min_length=1, max_length=100)]
    # 新增第一条地址时前端会传 true；库上有部分唯一索引，接口层负责先清掉旧的默认
    is_default: bool = False


class OrderItemIn(BaseModel):
    """「立即购买」时带的一行。来源是购物车时不传这个，服务端自己去车里取。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    product_id: SnowflakeRef
    quantity: Annotated[int, Field(ge=1, le=MAX_CART_QUANTITY)]


class OrderCreateIn(BaseModel):
    """下单。

    **不收金额**。总额一律由服务端按商品当前价格重算——客户端传来的价格在这里
    没有任何参考价值，收了反而会让人以为可以信。同理不收标题和封面，
    那些是服务端写快照时自己去查的。

    **items 两种来源都必传**，因为购物车页是可以勾选的——用户勾了三件里的两件，
    服务端无从猜起。让 'cart' 表示「整车结算」会直接把这个能力废掉。

    source 只决定一件事：**支付成功后要不要把这些商品从购物车里清掉**。
      'cart'   从购物车来，付完清掉对应的车行
      'direct' 商品详情页的「立即购买」，压根不碰购物车

    所以 source 是「清车意图」而不是「数据来源」。写成隐式规则（比如「商品在车里
    就顺手清掉」）会让详情页直接购买时莫名其妙地清掉用户车里的同款。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    address_id: SnowflakeRef
    source: Literal["cart", "direct"]
    items: Annotated[list[OrderItemIn], Field(min_length=1)]

    @field_validator("items")
    @classmethod
    def items_are_within_limits(cls, v: list[OrderItemIn]) -> list[OrderItemIn]:
        if len(v) > MAX_ORDER_ITEMS:
            raise ValueError(f"一单最多 {MAX_ORDER_ITEMS} 种商品")
        ids = [item.product_id for item in v]
        # 同一件商品出现两行，会让订单里出现两条一模一样的行、金额也翻倍。
        # 前端应当合并成一行并累加数量
        if len(set(ids)) != len(ids):
            raise ValueError("同一件商品不能重复提交")
        return v


class ShopOrderRemarkIn(BaseModel):
    """后台备注。允许清空（传空串），所以没有 min_length——
    写错了要能删掉，而不是只能改成另一句话。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    remark: Annotated[Trimmed, Field(max_length=500)] = ""


# 发货方式。取值与 schema.sql 的 shop_orders.shipping_type CHECK 逐字一致。
# 三档都是刚需：卖家具走专线或自送时没有运单号，只有「快递」这一档是不够的
SHIPPING_TYPES = ("express", "local", "none")
ShippingType = Literal["express", "local", "none"]


class ShopOrderShipIn(BaseModel):
    """发货。走快递必须给物流公司编码和运单号，另两档必须不给。

    这条规则库上也有（shop_orders_express_needs_tracking），两边都写是因为
    库上那条只能挡住写入，给不出一句人话；而这里挡不住绕过接口直接写库的情况。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    shipping_type: ShippingType
    # 微信的标准物流公司编码（如 SF）。常用的一份内置在 app/wxship.py，
    # 不在列表里也允许填——那份列表不全，挡住反而让运营发不了货
    shipping_company: Annotated[Trimmed, Field(max_length=40)] = ""
    tracking_no: Annotated[Trimmed, Field(max_length=60)] = ""

    @model_validator(mode="after")
    def express_needs_tracking(self) -> "ShopOrderShipIn":
        """走快递必须有物流公司和运单号。

        ⚠️ 必须是 **model 级**校验，不能写成 tracking_no 上的 field_validator：
        pydantic 默认不校验「用了默认值」的字段（validate_default=False），
        而「只传了 shippingType 和 shippingCompany」正是最常见的漏填形态——
        那时 tracking_no 取默认空串，field_validator 根本不会触发，
        空运单号会一路走到库上被 CHECK 挡下，变成 500 而不是一句人话。
        """
        if self.shipping_type != "express":
            return self
        if not self.tracking_no:
            raise ValueError("快递发货必须填运单号")
        if not self.shipping_company:
            raise ValueError("快递发货必须选物流公司")
        return self


class ShopOrderTrackingIn(BaseModel):
    """改运单号。只有快递单才有运单号可改，所以不带 shipping_type——
    改发货方式不是「改运单号」，那要先撤销发货，而我们不提供撤销。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    shipping_company: Annotated[Trimmed, Field(min_length=1, max_length=40)]
    tracking_no: Annotated[Trimmed, Field(min_length=1, max_length=60)]


class PaymentAnomalyResolveIn(BaseModel):
    """把一条支付异常标记为已处理。

    处理说明必填，且不允许清空：几个月后回看时，「谁在什么时候标了已处理」
    远不如「当时是怎么处理的」有用——这是资金异常的台账，不是待办清单。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    note: Annotated[Trimmed, Field(min_length=1, max_length=500)]


class ShopOrderRefundIn(BaseModel):
    """退款。原因必填——退款是不可逆的资金操作，事后要说得清为什么退。

    只做整单退，不做部分退（见 spec 决策）。所以这里没有金额字段：
    金额一律取订单的 total_cents，客户端说了不算。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    reason: Annotated[Trimmed, Field(min_length=1, max_length=200)]
