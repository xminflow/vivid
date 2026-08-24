"""安家立业的表单模型。选项值必须与 schema.anjia.sql 的 CHECK 约束逐字一致。

不并进 app/models.py：那份是安东尼之家的（预约、商品、订单），两个小程序的表单
彼此无关，混在一起以后只会越来越难看出哪个字段属于谁（docs/adr/0001 定的
「新代码按目标形态长在 app/anjia/ 下」）。
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pydantic.alias_generators import to_camel

Trimmed = Annotated[str, StringConstraints(strip_whitespace=True)]


class CertificationIn(BaseModel):
    """企业认证申请。

    只收这三个字段，**不收营业执照**：需求文档 4.3.2 明确认证从简、不做严格资质
    核验，而一旦收取资质材料就要承担存储与核验责任——收了不看比不收更糟。

    联系人与手机号不是可有可无的装饰：驳回之后能联系上对方的只有这两项。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    # 长度上下限与 schema.anjia.sql 的 CHECK 一致。要求「完整公司名称」，
    # 所以下限给 2 而不是 1，挡掉一个字的敷衍填写
    company_name: Annotated[Trimmed, Field(min_length=2, max_length=60)]
    contact_name: Annotated[Trimmed, Field(min_length=1, max_length=20)]
    # 与 app/models.py、两份 schema 的 CHECK 同一条正则
    contact_phone: Annotated[Trimmed, Field(pattern=r"^1[3-9]\d{9}$")]


class CertificationReviewIn(BaseModel):
    """驳回一份企业认证申请。

    理由**必填**：申请人看到的就是这句话，没有它他只能收到一个沉默的拒绝，
    既不知道该改什么，也只能靠反复提交去猜。min_length=1 配合 Trimmed，
    全是空格的「理由」等同于没填。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    reason: Annotated[Trimmed, Field(min_length=1, max_length=200)]


class CaseIn(BaseModel):
    """发布一条案例。

    只收标题、正文与一串图片**对象键**——宽高不在这里收：它由服务端在上传时读
    文件头算出来、焊进键里，发布时再从键上读回来（见 app/anjia/cases.py 的
    image_key_size）。让客户端报宽高的话，任何人都能报个 1×9999 把自己的卡片
    在双列瀑布流里撑成一整列。

    上下限与 schema.anjia.sql 的 CHECK 一致。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    title: Annotated[Trimmed, Field(min_length=1, max_length=30)]
    # 正文选填：企业发的是设计案例、新品、样板间，图是主体、字是配角
    body: Annotated[Trimmed, Field(max_length=1000)] = ""
    # 至少一张：没有图的案例在首页瀑布流里没有任何可呈现的形态。
    # 上限九张与微信 chooseMedia 的单次上限一致，作者不会遇到「选得到但发不了」
    images: Annotated[list[str], Field(min_length=1, max_length=9)]


class CaseReviewIn(BaseModel):
    """驳回一条案例。

    reason 是**给作者看的那句话**，必填，且短——它在「我的发布」的卡片上直接印出来。
    第一刀由管理员自己填，第二刀换成一组预设选项；两种都落进同一个字段，因为库里
    存的是**文案快照**而不是选项 code：预设项将来会增删改，存 code 的话旧记录会指
    向一个已经不存在的选项（同 reviewed_by 存用户名、订单行存商品快照的取舍）。

    note 是预设选项之外的补充说明，选填，同样原样回传给作者。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    reason: Annotated[Trimmed, Field(min_length=1, max_length=60)]
    note: Annotated[Trimmed, Field(max_length=200)] = ""
