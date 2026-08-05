"""日志初始化。

uvicorn 只给自己的 `uvicorn.*` logger 装了 handler，root logger 是光的，
业务模块的日志会落到 Python 的兜底 handler（`logging.lastResort`）——
没有时间戳、没有 logger 名，而且它只放行 WARNING 及以上，INFO 直接被丢掉。
不配这一下，「仅通过日志验证功能」就无从谈起。

级别由 LOG_LEVEL 控制，开发环境应设成 DEBUG。
"""

import logging
import os

DEFAULT_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "").strip().upper() or DEFAULT_LEVEL
    # 配错了直接抛。悄悄退回 INFO 会让人以为开了 DEBUG 却看不到日志，越查越偏
    if level not in logging.getLevelNamesMapping():
        raise ValueError(f"LOG_LEVEL 取值不合法：{level}")

    logging.basicConfig(level=level, format=LOG_FORMAT)
    # basicConfig 只设 root。业务包单独再设一次，这样即便 root 被别处调高，
    # 我们自己的模块该出的日志照样出得来
    logging.getLogger(__package__).setLevel(level)
