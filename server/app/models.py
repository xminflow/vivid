"""表单模型。选项值必须与 schema.sql 的 CHECK 约束逐字一致。"""

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
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


# 预约的跟进状态，与 schema.sql 的 appointments.status CHECK 逐字一致。
# 后台按它筛选，值不合法时 FastAPI 直接挡在接口外，不会带着脏值查库
APPOINTMENT_STATUSES = ("new", "confirmed", "visited", "cancelled")
AppointmentStatus = Literal["new", "confirmed", "visited", "cancelled"]


# ---------------------------------------------------------------------------
# 服务申请

SERVICE_IDS = ("design", "hardfit", "buyer", "aftersale", "resale")
ServiceId = Literal["design", "hardfit", "buyer", "aftersale", "resale"]

# 与 schema.sql 的 service_applications.status CHECK 逐字一致
SERVICE_APPLICATION_STATUSES = ("new", "contacted", "closed")
ServiceApplicationStatus = Literal["new", "contacted", "closed"]

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
HOME_SLOTS = ("hero", "showroom", "activity")
HomeSlot = Literal["hero", "showroom", "activity"]

# 每个位置最多放几张。数字按首页实际的版面定，不留「余量」——
# 上限放宽只会让运营多传的图悄悄不显示，还不如在后台就拦住
#   hero     首屏画廊五张
#   showroom 展厅参观动线六张
#   activity 首页是整张海报铺开、不是轮播，只能一张。库上也有唯一索引兜着
HOME_SLOT_MAX = {"hero": 5, "showroom": 6, "activity": 1}


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
