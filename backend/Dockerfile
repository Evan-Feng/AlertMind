# syntax=docker/dockerfile:1.7

# ===== Stage 1: builder =====
FROM python:3.11-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 从官方镜像复制 uv 可执行文件（避免网络安装步骤）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 先只拷贝依赖清单，利用 Docker 缓存
COPY pyproject.toml uv.lock ./

# 同步依赖（不安装项目本身，不装 dev 组）
RUN uv sync --frozen --no-install-project --no-dev

# 再拷贝源码并完成项目安装
COPY alertmind ./alertmind

RUN uv sync --frozen --no-dev


# ===== Stage 2: runtime =====
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# 创建非 root 运行用户
RUN groupadd --system --gid 1000 appuser \
    && useradd --system --uid 1000 --gid appuser --home-dir /app --shell /usr/sbin/nologin appuser

WORKDIR /app

# 从 builder 阶段复制已安装的虚拟环境 + 应用代码
COPY --from=builder --chown=appuser:appuser /app /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()"

CMD ["uvicorn", "alertmind.main:app", "--host", "0.0.0.0", "--port", "8000"]
