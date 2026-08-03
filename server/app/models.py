"""表单模型。选项值必须与 schema.sql 的 CHECK 约束逐字一致。"""

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from pydantic.alias_generators import to_camel

VISITOR_TYPES = ("C端业主", "设计师", "地产开发商", "酒店民宿业主", "家居行业经销商", "艺术家")
PURPOSES = ("展厅参观", "全案设计咨询", "装修与建材选购", "家居产品选购", "商务合作", "其他")
# 与小程序 mock/mine.js 的 genders 一致，多一个 '' 表示没填
GENDERS = ("", "女", "男", "不便告知")

VisitorType = Literal["C端业主", "设计师", "地产开发商", "酒店民宿业主", "家居行业经销商", "艺术家"]
Purpose = Literal["展厅参观", "全案设计咨询", "装修与建材选购", "家居产品选购", "商务合作", "其他"]
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


class AppointmentOut(BaseModel):
    """后台列表用。这里按库里的下划线字段直出，不转驼峰。"""

    id: int
    name: str
    phone: str
    visitor_type: str
    visit_date: date
    party_size: int
    purpose: str
    note: str
    space_id: str | None
    status: str
    created_at: object


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
        """只收本服务端签发过的对象键。

        客户端传来的是键不是 URL，若不校验，任何人都能把任意字符串写进库，
        后台按键签出的地址就会指向桶里别的对象。
        """
        total = sum(len(keys) for keys in v.values())
        if total > MAX_IMAGES:
            raise ValueError(f"图片最多 {MAX_IMAGES} 张")

        for keys in v.values():
            for key in keys:
                if not key.startswith("uploads/") or ".." in key:
                    raise ValueError("图片标识不合法")
        return v
