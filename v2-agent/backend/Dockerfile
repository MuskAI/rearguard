# 鉴真后端 — FastAPI + uv
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

# uv 二进制
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# 先装依赖（利用层缓存）
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 应用代码
COPY app ./app

ENV PATH="/app/.venv/bin:$PATH"
# 运行期环境变量由 docker-compose 的 env_file 注入（见 backend/.env）
EXPOSE 8848

CMD ["uv", "run", "--no-dev", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8848", "--no-access-log"]
