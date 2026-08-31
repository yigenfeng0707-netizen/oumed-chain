# ============================================================
# 瓯医数链 后端 Dockerfile（生产级 multi-stage）
# ============================================================

# ---------- Stage 1: Builder ----------
FROM python:3.11-slim AS builder

# 设置环境变量（避免写入 .pyc + 实时输出日志）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# 安装构建依赖（asyncpg 等需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装到独立目录
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

# ---------- Stage 2: Runtime ----------
FROM python:3.11-slim AS runtime

# 运行时环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/venv/bin:$PATH"

# 安装运行时依赖（libpq for asyncpg）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r yibao && useradd -r -g yibao -d /app -s /sbin/nologin yibao

WORKDIR /app

# 从 builder 复制已安装的依赖
COPY --from=builder --chown=yibao:yibao /install /usr/local

# 复制应用代码
COPY --chown=yibao:yibao . .

# 创建数据目录
RUN mkdir -p /app/chroma_data /app/data && chown -R yibao:yibao /app

# 切换非 root 用户
USER yibao

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=40s \
    CMD curl -f http://localhost:8000/api/health || exit 1

# 启动命令（生产配置：2 workers + proxy headers）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
