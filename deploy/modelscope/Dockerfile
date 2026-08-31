# ============================================================
# 瓯医数链 — 魔搭创空间全栈单容器镜像
# 架构：Next.js(standalone, 对外 7860) + FastAPI(内部 8000)
# 端口：对外固定 0.0.0.0:7860（魔搭平台强制）
# 说明：DEMO_OFFLINE=true 时后端全程离线降级（规则引擎），
#       无需 LLM API Key 即可演示全部核心链路。
# ============================================================

# ---------- Stage 1: 构建前端 (Next standalone) ----------
FROM node:18-bookworm-slim AS frontend-builder
WORKDIR /build/frontend
RUN corepack enable && corepack prepare pnpm@9 --activate

COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
# 同域部署：显式传空 → 前端 API_BASE 走相对路径 /api，无需知道公网域名
ENV NEXT_PUBLIC_API_URL=
ENV ENABLE_API_PROXY=1
RUN pnpm build

# ---------- Stage 2: 运行时 (Python + Node 双运行时) ----------
FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 复制 Node 运行时（均为 Debian/glibc 系二进制，可互拷）
COPY --from=frontend-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend-builder /usr/local/bin/npm /usr/local/bin/npm
COPY --from=frontend-builder /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 后端依赖（魔搭精简版：剔除 sentence-transformers/torch 等重型包）
COPY requirements-modelscope.txt ./
RUN pip install -r requirements-modelscope.txt

# 预置 ChromaDB 默认 ONNX 嵌入模型（~83MB）到镜像缓存。
# 若不预置，运行时首个 KB 检索请求会触发从 S3 下载（国外源，慢且不可控），
# 曾导致线上 chat 请求超时 504。
# 主源：ModelScope 自托管 HF 镜像（国内构建环境实测 ~7MB/s；
#       S3 直连仅 ~0.02MB/s，83MB 需 70 分钟，必超时）。
# 兜底：chromadb 官方 S3 tar.gz（海外 CI runner 走此源更快）。
# chromadb 检查 onnx/ 下 6 个文件齐全即跳过下载。
RUN set -e; \
    CACHE=/root/.cache/chroma/onnx_models/all-MiniLM-L6-v2; \
    mkdir -p "$CACHE/onnx"; \
    BASE="https://modelscope.cn/models/sentence-transformers/all-MiniLM-L6-v2/resolve/master"; \
    ok=1; \
    for f in config.json special_tokens_map.json tokenizer_config.json tokenizer.json vocab.txt onnx/model.onnx; do \
        out="$CACHE/onnx/$(basename "$f")"; \
        if curl -fsSL --retry 3 --connect-timeout 15 -o "$out.tmp" "$BASE/$f"; then \
            mv "$out.tmp" "$out"; \
        else \
            echo "[warn] ModelScope 镜像下载失败: $f"; ok=0; \
        fi; \
    done; \
    if [ "$ok" != "1" ]; then \
        echo "[info] 尝试 S3 兜底源..."; \
        if curl -fsSL --retry 2 --connect-timeout 15 -o "$CACHE/onnx.tar.gz" \
            "https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz" \
            && tar -xzf "$CACHE/onnx.tar.gz" -C "$CACHE"; then \
            rm -f "$CACHE/onnx.tar.gz"; \
            echo "[info] S3 兕底下载+解压成功"; \
        else \
            echo "[warn] ONNX 模型预置失败，运行时将按需下载（有超时降级保护）"; \
        fi; \
    fi; \
    ls -la "$CACHE/onnx" 2>/dev/null || true

# 验证模型可加载（本地缓存直接加载，正常应秒级完成）
RUN timeout 120 python -c "from chromadb.utils import embedding_functions as ef; ef.DefaultEmbeddingFunction()(['预热'])" \
    || echo "[warn] ONNX 模型加载验证失败，运行时将按需下载（有超时降级保护）"

# 后端源码
COPY backend/ ./backend/
# 数据文件：所有引擎用 /app/data/*.json 规则库（claims/health/policy/eeg/imaging）
# init_db.py 默认从 /app/data/mock_data.json 读取种子数据
COPY data/ ./data/

# 预构建知识库索引（policy_knowledge.json → chroma seed，仅索引不跑测试查询）。
# 运行时 entrypoint 将 seed 播种到 /mnt/workspace 持久化目录，
# 保证 RAG 检索开箱即用，且重启不丢。
# 加 timeout + 容错：依赖嵌入模型可用，失败不阻塞构建（运行时 KB 为空走降级）。
RUN cd /app/backend && timeout 300 python -c "import asyncio, json; from pathlib import Path; from app.services.knowledge_base import KnowledgeBase; docs = json.loads(Path('/app/data/policy_knowledge.json').read_text(encoding='utf-8')); kb = KnowledgeBase(embedding_api_key='', embedding_base_url='', embedding_model=''); asyncio.run(kb.initialize(persist_dir='/app/chroma_seed')); n = asyncio.run(kb.index_documents(docs)); print('KB seed indexed chunks:', n)" \
    || echo "[warn] KB seed 构建失败/超时，运行时 KB 为空将走降级"

# 持久化目录：/mnt/workspace 为魔搭创空间持久化挂载点（重启/迁移不丢数据）
RUN mkdir -p /app/backend/data /mnt/workspace/data

# 前端 standalone 产物 + 静态资源
# ⚠️ standalone 输出不包含 public/，必须显式复制
# （DidaYi 品牌 LOGO 等 /branding/* 资源在此目录，遗漏会 404）
COPY --from=frontend-builder /build/frontend/.next/standalone ./frontend
COPY --from=frontend-builder /build/frontend/.next/static ./frontend/.next/static
COPY --from=frontend-builder /build/frontend/public ./frontend/public

COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

# 魔搭强制：对外 7860
EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=90s \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["./entrypoint.sh"]
