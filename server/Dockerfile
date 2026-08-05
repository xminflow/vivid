# 构建阶段：只在这里装依赖，产物是一个自包含的 .venv
FROM python:3.12-slim AS builder

# uv 版本与本地锁文件保持一致，避免解析行为差异
COPY --from=ghcr.io/astral-sh/uv:0.9.14 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    # psycopg-binary 等大 wheel 在默认 30s 内拉不完，会构建失败
    UV_HTTP_TIMEOUT=180

WORKDIR /app

# 依赖单独成层：只改业务代码时这一层命中缓存，不重装依赖
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project


# 运行阶段：不带 uv、不带构建缓存，只留 venv 和业务代码
FROM python:3.12-slim

# COS 对象键的日期前缀取的是本地时间（app/cos.py build_key），容器默认 UTC
# 会让 08:00 前的上传落到前一天的目录，与开发环境不一致，所以装时区数据
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# 不用 root 跑服务
RUN useradd --create-home --uid 10001 app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app app ./app

USER app

# 容器内监听端口，HEALTHCHECK 和 CMD 都读它，运行时可用 -e PORT 覆盖。
# 本地开发（README 里直接跑 uvicorn）仍是 3001，两者不必一致：
# 宿主机怎么映射由 -p 决定，小程序 utils/config.js 的 API_BASE 跟宿主机端口走
ENV PORT=3000
EXPOSE 3000

# /health 会在连接池打开后才可用，所以它同时反映了数据库是否连得上
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen(f\"http://127.0.0.1:{os.environ['PORT']}/health\", timeout=3).status == 200 else 1)"

# exec 让 uvicorn 成为 PID 1，容器停止时能收到 SIGTERM 正常关闭连接池
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
