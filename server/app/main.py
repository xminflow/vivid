"""小程序接口：展厅预约登记、服务申请、COS 图片直传地址签发。"""

from contextlib import asynccontextmanager

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from psycopg.types.json import Json

from . import cos
from .db import pool
from .models import AppointmentIn, ServiceApplicationIn, UploadUrlIn
from .users import current_user_or_none
from .users import router as users_router

# 校验失败时给用户看的话。pydantic 的英文报错不适合直接弹给用户
FIELD_MESSAGES = {
    "name": "请填写您的称呼",
    "phone": "电话格式不正确",
    "visitorType": "请选择来者身份",
    "visitDate": "请选择到访日期",
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
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    await pool.open(wait=True, timeout=10)
    yield
    await pool.close()


app = FastAPI(title="展厅预约登记", lifespan=lifespan)

# 开发者工具与本地后台会跨域访问；上线前收窄到具体域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(users_router)


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
                      (name, phone, visitor_type, visit_date, party_size, purpose,
                       note, space_id, user_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, created_at
                    """,
                    (
                        form.name,
                        form.phone,
                        form.visitor_type,
                        form.visit_date,
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


@app.get("/api/appointments")
async def list_appointments() -> JSONResponse:
    """给后台临时看数据用。上线前必须加鉴权，否则客户手机号裸奔。"""
    try:
        async with pool.connection() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT id, name, phone, visitor_type, visit_date, party_size,
                           purpose, note, space_id, status, created_at
                      FROM appointments
                     ORDER BY created_at DESC
                     LIMIT 100
                    """
                )
            ).fetchall()
    except psycopg.Error as exc:
        print(f"[list failed] {exc}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"ok": False, "message": "查询失败"},
        )

    items = [
        {
            **row,
            "visit_date": row["visit_date"].isoformat(),
            "created_at": row["created_at"].isoformat(),
        }
        for row in rows
    ]
    return JSONResponse(content={"ok": True, "total": len(items), "items": items})


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


@app.get("/api/service-applications")
async def list_service_applications(service_id: str | None = None) -> JSONResponse:
    """给后台临时看数据用。跟 /api/appointments 一样，上线前必须加鉴权。"""
    sql = """
        SELECT id, service_id, name, phone, fields, images, status, created_at
          FROM service_applications
    """
    params: tuple = ()
    if service_id:
        sql += " WHERE service_id = %s"
        params = (service_id,)
    sql += " ORDER BY created_at DESC LIMIT 100"

    try:
        async with pool.connection() as conn:
            rows = await (await conn.execute(sql, params)).fetchall()
    except psycopg.Error as exc:
        print(f"[service application list failed] {exc}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"ok": False, "message": "查询失败"},
        )

    items = []
    for row in rows:
        # 库里存的是对象键，这里现签成一小时有效的地址，后台才看得到图
        images = {
            group: [cos.presign_get(key) for key in keys]
            for group, keys in (row["images"] or {}).items()
        } if cos.configured() else {}
        items.append(
            {**row, "images": images, "created_at": row["created_at"].isoformat()}
        )

    return JSONResponse(content={"ok": True, "total": len(items), "items": items})
