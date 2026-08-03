"""连接池。连接串放在 .env 里，不进版本库。"""

import os
from pathlib import Path

from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

# 从仓库根的 .env 读，无论从哪个目录启动都找得到
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("缺少 DATABASE_URL：把 .env.example 复制成 .env 并填上连接串")

pool = AsyncConnectionPool(
    DATABASE_URL,
    min_size=1,
    max_size=5,
    open=False,
    kwargs={"row_factory": dict_row},
)
