"""在**原生 Windows** 上起本地开发服务。Linux / WSL / 容器仍然用 `uvicorn app.main:app`。

为什么需要它：psycopg 的异步模式要求事件循环支持 `add_reader`，而 Windows 上
asyncio 默认的 `ProactorEventLoop` 没有这个方法。用默认循环起服务的话，连接池
连一条都建不起来，`lifespan` 里的 `pool.open(wait=True, timeout=10)` 直接
`PoolTimeout: pool initialization incomplete after 10 sec` 然后
`Application startup failed`——看着像连不上库，实际是循环选错了。

为什么不能在 `app/main.py` 里 `set_event_loop_policy` 了事（试过，无效）：
uvicorn 从 0.36 起不再读 asyncio 的 event loop policy。`uvicorn/loops/asyncio.py`
的 `asyncio_loop_factory()` 里写死了「win32 且非 subprocess → ProactorEventLoop」，
经 `Config.get_loop_factory()` 交给 `uvicorn/server.py` 的
`asyncio_run(..., loop_factory=...)`，policy 根本不在这条链路上。而且就算还读
policy 也来不及：import `app.main` 发生在 `Server._serve()` 里的 `config.load()`，
那时循环早就建好了。所以只能反过来——自己先建好循环，再把 uvicorn 跑进去。

用法（在 server 目录下）：
    uv run python scripts/dev_server.py
    uv run python scripts/dev_server.py --port 3001 --log-level debug

**不支持 `--reload`**：reload 模式下真正跑服务的是 uvicorn 自己 spawn 的子进程，
子进程里的循环由 uvicorn 决定，从这里绕不过去。改完代码手动重启。
需要 reload 就在 WSL 里用 `uv run uvicorn app.main:app --reload`。
"""

import argparse
import asyncio
import sys
from pathlib import Path

import uvicorn

# 允许从任意目录 `python scripts/dev_server.py`：app 包在本文件的上一级
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="在原生 Windows 上起本地开发服务")
    # 默认只听本机回环。要让手机或微信开发者工具从别的机器连进来再显式改 --host
    parser.add_argument("--host", default="127.0.0.1")
    # 3000 是全链路统一的端口：容器内监听、小程序 utils/config.js 的 API_BASE 都用它
    parser.add_argument("--port", type=int, default=3000)
    # 不给默认值：留空时由 app/logging_setup.py 按 .env 的 LOG_LEVEL 决定
    parser.add_argument("--log-level", default=None)
    args = parser.parse_args()

    server = uvicorn.Server(
        uvicorn.Config(
            "app.main:app", host=args.host, port=args.port, log_level=args.log_level
        )
    )

    # 直接给 Runner 指定 loop_factory，而不是 set_event_loop_policy：
    # 两者在 3.12 上效果一样，但 policy 那套 API 从 3.14 起弃用，而 loop_factory
    # 正是 uvicorn 自己在用的入口（见上面 docstring）。
    # 非 Windows 传 None，就是 asyncio 的默认行为，这个脚本在 Linux 上照样能跑
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(server.serve())


if __name__ == "__main__":
    main()
