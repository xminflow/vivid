"""小程序接口：展厅预约登记、服务申请、COS 图片直传地址签发。"""

import logging
import asyncio
import os
from contextlib import asynccontextmanager, suppress

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from psycopg.types.json import Json

from . import cos, snowflake, wxpay
from .admin import router as admin_router
from .admin_accounts import router as admin_accounts_router
from .admin_auth import router as admin_auth_router
from .anjia import db as anjia_db
from .anjia.admin import router as anjia_admin_router
from .anjia.cases import router as anjia_cases_router
from .anjia.cases_admin import router as anjia_cases_admin_router
from .anjia.users import router as anjia_users_router
from .db import pool
from .home import router as home_router
from .logging_setup import setup_logging
from .models import AppointmentIn, ServiceApplicationIn, UploadUrlIn
from .payment_anomalies import router as payment_anomalies_router
from .shop import router as shop_router
from .shop_addresses import router as shop_addresses_router
from .shop_admin import router as shop_admin_router
from .shop_orders import router as shop_orders_router
from .shop_orders_admin import router as shop_orders_admin_router
from .shop_pay import router as shop_pay_router
from .users import current_user_or_none
from .users import router as users_router

# 这里**不要**加 asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())。
# 试过，无效：uvicorn 0.36 起改用 Config.get_loop_factory() + asyncio.Runner，
# 不再读 policy；而且 import 本模块时循环已经建好了。原生 Windows 上起本地服务
# 请用 scripts/dev_server.py，那里解释了完整原因。
# 测试侧走 anyio/pytest-asyncio，那条路径确实认 policy，所以 tests/conftest.py
# 里那一份是有效的，不要照着它在这里也来一份

# 在建 app 之前配好，业务模块之后打的日志才有格式、INFO 才出得来
setup_logging()

logger = logging.getLogger(__name__)

# 校验失败时给用户看的话。pydantic 的英文报错不适合直接弹给用户
FIELD_MESSAGES = {
    "name": "请填写您的称呼",
    "phone": "电话格式不正确",
    "visitorType": "请选择来者身份",
    "visitDate": "请选择到访日期",
    "visitTime": "请选择到访时间",
    "partySize": "到访人数需在 1 至 50 之间",
    "purpose": "请选择预约需求",
    "note": "备注过长",
    # 「我的信息」与登录
    "memberName": "称呼过长",
    "email": "邮箱格式不正确",
    "birthday": "生日不在可选范围内",
    "gender": "性别选项不正确",
    "region": "地区选择不正确",
    "avatarKey": "头像标识不合法",
    "code": "登录信息缺失，请重试",
    # 管理后台登录
    "username": "用户名要 3-32 位，只能用字母、数字和 _ . -",
    "password": "密码要 8 到 64 位",
    "oldPassword": "请输入当前密码",
    "newPassword": "新密码要 8 到 64 位",
    "displayName": "姓名过长",
    # 安家立业的企业认证申请
    "companyName": "请填写完整的公司名称",
    "contactName": "请填写联系人姓名",
    "contactPhone": "联系电话格式不正确",
}


async def _sweep_loop() -> None:
    """每 60 秒扫一轮：关掉超时未付的单，把发货满 10 天的置为已完成。

    在 lifespan 里起一个 asyncio 任务，不引入 Celery 或 APScheduler：
    这两件事各只有一个动作、没有重试语义、失败了下一轮自然重来，
    为它们加一个任务队列和一个 broker 不划算。

    异常一律吞掉并记日志——扫描任务崩了不能把整个服务带下去，
    但必须留下痕迹，否则超时单会悄悄堆积而没人知道。

    两件事分别 try：它们互不依赖，一件失败不该让另一件也停。
    自动确认收货不碰微信，不该被「微信不通」连累。

    退款对账是**每天一次**，不跟着这个节奏跑：它下载的是微信的日账单，
    一天之内重复下载没有任何新信息。计时用进程内的时间戳，不落库——
    重启后会多跑一次，而对账本身是幂等的，多跑一次只是多几 KB 下载。
    """
    from .shop_pay import sweep_auto_receipt, sweep_expired_orders
    from .shop_reconcile import reconcile_refunds

    last_reconcile = 0.0

    while True:
        await asyncio.sleep(60)
        for name, job in (
            ("超时关单", sweep_expired_orders),
            ("自动确认收货", sweep_auto_receipt),
        ):
            try:
                await job()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("%s出错，下一轮继续", name)

        now = asyncio.get_running_loop().time()
        if now - last_reconcile >= 24 * 60 * 60:
            last_reconcile = now
            try:
                await reconcile_refunds()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("退款对账出错，明天再跑")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await pool.open(wait=True, timeout=10)

    # 安家立业是另一个库、另一个池（docs/adr/0001）。没配就不开池、不注册路由，
    # 安东尼之家照常服务——理由见 app/anjia/db.py，是部署顺序而不是怕报错
    if anjia_db.configured():
        await anjia_db.get_pool().open(wait=True, timeout=10)
        logger.info("安家立业已接入，接口在 /api/anjia/ 下")
    else:
        logger.warning("未配置 DATABASE_URL_ANJIA，安家立业的接口不注册")
    # 机器号在启动时就定下来并打进日志，不等第一个用户注册才初始化——
    # 多实例撞号必须在启动阶段就能对着日志看出来
    logger.info("服务启动完成，雪花 ID 机器号 %d", snowflake.worker_id())

    # 扫描任务**总是**起：它做两件事，只有「超时关单」依赖支付配置
    # （那一半会自己返回 0），「发货满 10 天自动确认收货」纯粹是本地状态迁移。
    # 支付没配时把缺了哪几项写进日志——这是判断「这台机器有没有接支付」最快的地方
    sweeper = asyncio.create_task(_sweep_loop())
    if wxpay.configured():
        logger.info("订单扫描已启动，间隔 60 秒，支付超时 %d 分钟", wxpay.PAY_TIMEOUT_MINUTES)
    else:
        logger.warning(
            "订单扫描已启动，但支付未配置、超时关单不会生效。缺：%s",
            "、".join(wxpay.missing_config()),
        )

    yield

    if sweeper is not None:
        sweeper.cancel()
        with suppress(asyncio.CancelledError):
            await sweeper
    await pool.close()
    if anjia_db.configured():
        await anjia_db.get_pool().close()


app = FastAPI(title="展厅预约登记", lifespan=lifespan)

# 跨域。**线上其实用不到**：Caddy 把 /api/* 和前端产物放在同一个域名下
# （见 deploy/Caddyfile），后台和官网请求接口都是同源的；小程序的 wx.request
# 不走浏览器的同源策略，压根不发 Origin。真正需要跨域的只有本地开发——
# vite 跑在 5180 / 5190，接口在 3000。
#
# 所以默认值就是那几个本地地址，而不是 "*"。用 "*" 的代价不是「立刻能被利用」
# （鉴权是 Bearer token 不是 Cookie，构不成 CSRF），而是任何一个网页都能拿着
# 用户的 token 直接读我们的接口——一旦将来某处改用 Cookie 或加了别的凭据，
# 这个通配符就从「暂时无害」变成一个洞，而那时没有人会想起来回头收窄它。
#
# 要放开别的来源在 .env 里配 CORS_ORIGINS（逗号分隔的完整来源，含协议和端口）
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5180,http://127.0.0.1:5180,"
        "http://localhost:5190,http://127.0.0.1:5190",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
logger.info("跨域放行来源：%s", "、".join(CORS_ORIGINS) or "（无）")

# 登录接口自己不能要求登录，所以它是独立的 router，不带 admin_router 上那个鉴权依赖
app.include_router(admin_auth_router)
app.include_router(users_router)
app.include_router(home_router)
app.include_router(shop_router)
app.include_router(shop_addresses_router)
app.include_router(shop_orders_router)
app.include_router(shop_pay_router)
app.include_router(admin_router)
app.include_router(admin_accounts_router)
app.include_router(shop_admin_router)
app.include_router(shop_orders_admin_router)
app.include_router(payment_anomalies_router)

# 安家立业。库没配时整组不挂——挂了也只会在第一次请求时抛 RuntimeError，
# 那时报错离配置缺失已经隔了很远，不如启动日志里的那句 warning 直白
if anjia_db.configured():
    app.include_router(anjia_users_router)
    app.include_router(anjia_cases_router)
    app.include_router(anjia_admin_router)
    app.include_router(anjia_cases_admin_router)


@app.exception_handler(RequestValidationError)
async def on_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    messages: list[str] = []
    for err in exc.errors():
        # 字段上自定义的 ValueError（如「到访日期不能早于今天」）本身就是中文，直接用
        if err["type"] == "value_error":
            messages.append(err["msg"].removeprefix("Value error, "))
            continue
        field = str(err["loc"][-1]) if err["loc"] else ""
        messages.append(FIELD_MESSAGES.get(field, "提交内容有误，请检查后重试"))

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"ok": False, "message": messages[0], "errors": messages},
    )


@app.exception_handler(HTTPException)
async def on_http_error(_: Request, exc: HTTPException) -> JSONResponse:
    """统一成 {ok, message}，前端一套判断就够，不用分辨 detail 还是 message。"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "message": exc.detail},
        headers=exc.headers,
    )


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.post("/api/appointments", status_code=status.HTTP_201_CREATED)
async def create_appointment(
    form: AppointmentIn,
    user: dict | None = Depends(current_user_or_none),
) -> JSONResponse:
    """未登录也能提交；带了登录态就记上是谁提交的，「我的」页才能拉自己的记录。"""
    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    INSERT INTO appointments
                      (name, phone, visitor_type, visit_date, visit_time, party_size,
                       purpose, note, space_id, user_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, created_at
                    """,
                    (
                        form.name,
                        form.phone,
                        form.visitor_type,
                        form.visit_date,
                        form.visit_time,
                        form.party_size,
                        form.purpose,
                        form.note,
                        form.space_id,
                        user["id"] if user else None,
                    ),
                )
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        # 同一手机号同一天已登记过
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"ok": False, "message": "这个号码当天已有预约，换个日期或直接联系我们"},
        )
    except psycopg.Error as exc:
        print(f"[insert failed] {exc}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"ok": False, "message": "提交失败，请稍后再试"},
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "ok": True,
            "id": row["id"],
            "createdAt": row["created_at"].isoformat(),
        },
    )


# 后台看列表的接口搬到了 app/admin.py 的 GET /api/admin/appointments：
# 那边有分页和筛选，也是将来统一加鉴权的地方。这里不再留一份只能出 100 条的临时实现。


# ---------------------------------------------------------------------------
# 图片直传


@app.post("/api/upload-url")
async def create_upload_url(req: UploadUrlIn) -> JSONResponse:
    """签发一个短时效的 COS 直传地址。

    小程序不持有密钥，也不经服务端中转图片：服务端只签地址，字节由客户端直接
    发往 COS。地址只对这一个对象键、只允许 PUT、15 分钟过期。
    """
    try:
        key = cos.build_key(req.scene, req.ext)
        url = cos.presign_put(key)
    except cos.CosNotConfigured as exc:
        print(f"[cos not configured] {exc}")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"ok": False, "message": "图片服务未配置，请联系我们"},
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"ok": False, "message": str(exc)},
        )

    return JSONResponse(
        content={"ok": True, "key": key, "url": url, "maxBytes": cos.MAX_UPLOAD_BYTES}
    )


# ---------------------------------------------------------------------------
# 服务申请


@app.post("/api/service-applications", status_code=status.HTTP_201_CREATED)
async def create_service_application(form: ServiceApplicationIn) -> JSONResponse:
    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    INSERT INTO service_applications
                      (service_id, name, phone, fields, images)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id, created_at
                    """,
                    (
                        form.service_id,
                        form.name,
                        form.phone,
                        Json(form.fields),
                        Json(form.images),
                    ),
                )
            ).fetchone()
    except psycopg.Error as exc:
        print(f"[service application insert failed] {exc}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"ok": False, "message": "提交失败，请稍后再试"},
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "ok": True,
            "id": row["id"],
            "createdAt": row["created_at"].isoformat(),
        },
    )


# 同上，列表见 GET /api/admin/service-applications。
